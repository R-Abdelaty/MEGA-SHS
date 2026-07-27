"""Read-only extraction tool for schedule files stored in ``fake data``."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from langchain.tools import tool
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries
from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FAKE_DATA_DIR = (PROJECT_ROOT / "fake data").resolve()
SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".pdf"}
DEFAULT_MAX_ROWS = 100
MAX_ROW_LIMIT = 500
DEFAULT_MAX_PAGES = 10
MAX_PAGE_LIMIT = 50
DEFAULT_MAX_CHARS = 40_000
MAX_CHAR_LIMIT = 120_000
MAX_CELL_CHARS = 2_000


def _json(data: dict[str, Any]) -> str:
    """Return deterministic, Unicode-safe JSON for the model."""
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _error(code: str, message: str, **details: Any) -> str:
    payload: dict[str, Any] = {
        "status": "error",
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details:
        payload["error"]["details"] = details
    return _json(payload)


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _available_files() -> list[dict[str, Any]]:
    if not FAKE_DATA_DIR.is_dir():
        return []

    files: list[dict[str, Any]] = []
    for path in sorted(FAKE_DATA_DIR.iterdir(), key=lambda item: item.name.casefold()):
        if (
            path.is_file()
            and not path.name.startswith("~$")
            and path.suffix.casefold() in SUPPORTED_EXTENSIONS
        ):
            files.append(
                {
                    "name": path.name,
                    "stem": path.stem,
                    "format": path.suffix.casefold().lstrip("."),
                    "size_bytes": path.stat().st_size,
                }
            )
    return files


def _resolve_requested_file(requested_value: str) -> tuple[Path | None, str | None]:
    """Resolve a user-supplied name while preventing access outside fake data."""
    requested_value = requested_value.strip().strip("\"'")
    if not requested_value:
        return None, _error(
            "missing_file_name",
            "A file name is required.",
            available_files=_available_files(),
        )

    requested = Path(requested_value)
    if requested.is_absolute():
        resolved = requested.resolve()
        if not _is_inside(resolved, FAKE_DATA_DIR):
            return None, _error(
                "path_outside_fake_data",
                "The tool can only read files inside the fake data folder.",
            )
        if resolved.is_file():
            return resolved, None
    else:
        parts = list(requested.parts)
        if parts and parts[0].casefold().replace("_", " ") == "fake data":
            parts = parts[1:]
        relative = Path(*parts) if parts else Path()
        candidate = (FAKE_DATA_DIR / relative).resolve()
        if not _is_inside(candidate, FAKE_DATA_DIR):
            return None, _error(
                "path_outside_fake_data",
                "The requested relative path leaves the fake data folder.",
            )
        if candidate.is_file():
            return candidate, None

    available_paths = [
        path
        for path in FAKE_DATA_DIR.iterdir()
        if path.is_file()
        and not path.name.startswith("~$")
        and path.suffix.casefold() in SUPPORTED_EXTENSIONS
    ]
    requested_name = requested.name.casefold()
    requested_stem = requested.stem.casefold()

    exact_matches = [
        path
        for path in available_paths
        if path.name.casefold() == requested_name
        or (not requested.suffix and path.stem.casefold() == requested_name)
    ]
    if len(exact_matches) == 1:
        return exact_matches[0].resolve(), None

    partial_matches = [
        path
        for path in available_paths
        if requested_stem and requested_stem in path.stem.casefold()
    ]
    if len(partial_matches) == 1:
        return partial_matches[0].resolve(), None
    if len(partial_matches) > 1:
        return None, _error(
            "ambiguous_file_name",
            "The requested name matches multiple files. Use an exact file name.",
            matches=[path.name for path in sorted(partial_matches)],
        )

    return None, _error(
        "file_not_found",
        "No matching Excel or PDF file was found in the fake data folder.",
        requested=requested_value,
        available_files=_available_files(),
    )


def _safe_value(value: Any) -> Any:
    """Convert spreadsheet values to compact JSON-safe values."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (int, float, bool)):
        return value

    text = str(value).replace("\x00", "").strip()
    if len(text) > MAX_CELL_CHARS:
        return text[: MAX_CELL_CHARS - 1] + "…"
    return text


def _row_matches(values: list[Any], query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(needle in str(value).casefold() for value in values if value is not None)


def _unique_headers(raw_headers: list[Any]) -> list[str]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(raw_headers, start=1):
        base = str(value).strip() if value not in (None, "") else f"column_{index}"
        counts[base] = counts.get(base, 0) + 1
        headers.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return headers


class _ExtractionBudget:
    """Shared row/character budget across all returned tables or pages."""

    def __init__(self, max_rows: int, max_chars: int) -> None:
        self.rows_remaining = max_rows
        # Leave room for workbook metadata and JSON structure.
        self.chars_remaining = max(2_000, max_chars - 8_000)
        self.truncated = False

    def accept(self, item: Any) -> bool:
        estimated_chars = len(json.dumps(item, ensure_ascii=False, default=str))
        if self.rows_remaining <= 0 or estimated_chars > self.chars_remaining:
            self.truncated = True
            return False
        self.rows_remaining -= 1
        self.chars_remaining -= estimated_chars
        return True


def _resolve_sheet_names(workbook: Any, sheet_name: str | None, query: str | None) -> tuple[list[str], str | None]:
    names = list(workbook.sheetnames)
    if not sheet_name:
        if query:
            return names, None
        preferred = next(
            (
                name
                for name in ("Overview", "Doctor Directory", "Semester Timetable", "Room Inventory")
                if name in names
            ),
            names[0] if names else None,
        )
        return ([preferred] if preferred else []), None

    matches = [name for name in names if name.casefold() == sheet_name.strip().casefold()]
    if len(matches) == 1:
        return matches, None
    return [], _error(
        "sheet_not_found",
        "The requested worksheet was not found.",
        requested_sheet=sheet_name,
        available_sheets=names,
    )


def _worksheet_index(workbook: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": sheet.title,
            "max_row": sheet.max_row,
            "max_column": sheet.max_column,
            "table_names": list(sheet.tables.keys()),
        }
        for sheet in workbook.worksheets
    ]


def _extract_excel(
    path: Path,
    sheet_name: str | None,
    query: str | None,
    max_rows: int,
    max_chars: int,
) -> str:
    workbook = load_workbook(path, read_only=False, data_only=True)
    selected_names, sheet_error = _resolve_sheet_names(workbook, sheet_name, query)
    if sheet_error:
        workbook.close()
        return sheet_error

    budget = _ExtractionBudget(max_rows=max_rows, max_chars=max_chars)
    extracted_sheets: list[dict[str, Any]] = []
    total_matches = 0

    for name in selected_names:
        sheet = workbook[name]
        sheet_payload: dict[str, Any] = {
            "name": name,
            "dimensions": {
                "max_row": sheet.max_row,
                "max_column": sheet.max_column,
            },
            "tables": [],
        }

        # Capture the common title/subtitle convention without assuming it exists.
        context_rows: list[dict[str, Any]] = []
        for row_number in range(1, min(sheet.max_row, 3) + 1):
            values = [
                _safe_value(sheet.cell(row=row_number, column=column).value)
                for column in range(1, min(sheet.max_column, 20) + 1)
            ]
            nonempty = [value for value in values if value not in (None, "")]
            if nonempty:
                context_rows.append({"excel_row": row_number, "values": nonempty})
        if context_rows:
            sheet_payload["context"] = context_rows

        table_match_count = 0
        for table in sheet.tables.values():
            min_col, min_row, max_col, max_row = range_boundaries(table.ref)
            raw_headers = [
                sheet.cell(row=min_row, column=column).value
                for column in range(min_col, max_col + 1)
            ]
            headers = _unique_headers(raw_headers)
            returned_rows: list[dict[str, Any]] = []

            for row_number in range(min_row + 1, max_row + 1):
                raw_values = [
                    sheet.cell(row=row_number, column=column).value
                    for column in range(min_col, max_col + 1)
                ]
                if not _row_matches(raw_values, query):
                    continue
                record = {
                    "excel_row": row_number,
                    "values": {
                        header: _safe_value(value)
                        for header, value in zip(headers, raw_values)
                    },
                }
                if not budget.accept(record):
                    break
                returned_rows.append(record)
                table_match_count += 1
                total_matches += 1

            if returned_rows or not query:
                sheet_payload["tables"].append(
                    {
                        "name": table.name,
                        "range": table.ref,
                        "columns": headers,
                        "returned_row_count": len(returned_rows),
                        "rows": returned_rows,
                    }
                )
            if budget.rows_remaining <= 0:
                break

        # Fallback for sheets without Excel tables, or for a query not found in tables.
        if not sheet.tables or (query and table_match_count == 0):
            raw_matches: list[dict[str, Any]] = []
            for row_number in range(1, sheet.max_row + 1):
                raw_values = [
                    sheet.cell(row=row_number, column=column).value
                    for column in range(1, sheet.max_column + 1)
                ]
                if not _row_matches(raw_values, query):
                    continue
                nonempty_cells = {
                    get_column_letter(column): _safe_value(value)
                    for column, value in enumerate(raw_values, start=1)
                    if value not in (None, "")
                }
                if not nonempty_cells:
                    continue
                record = {"excel_row": row_number, "cells": nonempty_cells}
                if not budget.accept(record):
                    break
                raw_matches.append(record)
                total_matches += 1
            if raw_matches:
                sheet_payload["raw_rows"] = raw_matches

        if sheet_payload["tables"] or sheet_payload.get("raw_rows") or not query:
            extracted_sheets.append(sheet_payload)
        if budget.rows_remaining <= 0:
            break

    payload = {
        "status": "ok",
        "file": {
            "name": path.name,
            "format": path.suffix.casefold().lstrip("."),
            "size_bytes": path.stat().st_size,
        },
        "workbook": {
            "sheet_count": len(workbook.sheetnames),
            "sheets": _worksheet_index(workbook),
        },
        "request": {
            "sheet_name": sheet_name,
            "query": query,
        },
        "extraction": {
            "selected_sheets": selected_names,
            "matching_rows_returned": total_matches,
            "sheets": extracted_sheets,
        },
        "limits": {
            "max_rows": max_rows,
            "max_chars": max_chars,
            "truncated": budget.truncated,
            "note": (
                "Request a specific sheet or add a query to narrow the result."
                if budget.truncated
                else None
            ),
        },
    }
    workbook.close()
    return _json(payload)


def _clean_pdf_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _query_snippet(text: str, query: str, radius: int = 800) -> str:
    position = text.casefold().find(query.casefold())
    if position < 0:
        return text
    start = max(0, position - radius)
    end = min(len(text), position + len(query) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def _extract_pdf(
    path: Path,
    page_number: int | None,
    query: str | None,
    max_pages: int,
    max_chars: int,
) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted and reader.decrypt("") == 0:
        return _error(
            "encrypted_pdf",
            "The PDF is encrypted and cannot be read without a password.",
            file=path.name,
        )

    page_count = len(reader.pages)
    if page_number is not None:
        if page_number < 1 or page_number > page_count:
            return _error(
                "page_out_of_range",
                "The requested PDF page is outside the document.",
                requested_page=page_number,
                page_count=page_count,
            )
        page_indexes = [page_number - 1]
    else:
        page_indexes = list(range(page_count))

    pages: list[dict[str, Any]] = []
    chars_remaining = max(1_000, max_chars - 4_000)
    truncated = False
    matched_pages = 0

    for page_index in page_indexes:
        text = _clean_pdf_text(reader.pages[page_index].extract_text() or "")
        if query and query.casefold() not in text.casefold():
            continue
        if query:
            text = _query_snippet(text, query)
        matched_pages += 1

        if len(pages) >= max_pages:
            truncated = True
            break
        if len(text) > chars_remaining:
            text = text[: max(0, chars_remaining - 1)] + "…"
            truncated = True
        pages.append(
            {
                "page_number": page_index + 1,
                "text": text,
            }
        )
        chars_remaining -= len(text)
        if chars_remaining <= 0:
            truncated = True
            break

    metadata = reader.metadata or {}
    payload = {
        "status": "ok",
        "file": {
            "name": path.name,
            "format": "pdf",
            "size_bytes": path.stat().st_size,
        },
        "pdf": {
            "page_count": page_count,
            "metadata": {
                "title": _safe_value(metadata.get("/Title")),
                "author": _safe_value(metadata.get("/Author")),
                "subject": _safe_value(metadata.get("/Subject")),
            },
        },
        "request": {
            "page_number": page_number,
            "query": query,
        },
        "extraction": {
            "matched_page_count": matched_pages,
            "pages": pages,
        },
        "limits": {
            "max_pages": max_pages,
            "max_chars": max_chars,
            "truncated": truncated,
            "note": (
                "Request a specific page or add a query to narrow the result."
                if truncated
                else None
            ),
        },
    }
    return _json(payload)


@tool
def get_schedule(
    uploaded_file_path: str,
    sheet_name: str | None = None,
    query: str | None = None,
    page_number: int | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Extract AI-ready data from an Excel or PDF file in the fake data folder.

    Supply an exact file name when possible. For Excel files, optionally provide
    ``sheet_name`` and/or a case-insensitive ``query`` to narrow the returned
    table records. Without either, the tool returns the workbook index and its
    overview/directory/first useful sheet. For PDFs, optionally provide a
    one-based ``page_number`` or ``query``. Results are structured JSON and are
    bounded by row, page, and character limits.
    """
    if not FAKE_DATA_DIR.is_dir():
        return _error(
            "fake_data_folder_missing",
            "The fake data folder does not exist.",
            expected_path=str(FAKE_DATA_DIR),
        )

    path, resolution_error = _resolve_requested_file(uploaded_file_path)
    if resolution_error:
        return resolution_error
    assert path is not None

    if path.name.startswith("~$"):
        return _error(
            "temporary_office_file",
            "Temporary Office lock files cannot be read. Request the real workbook instead.",
            file=path.name,
        )

    extension = path.suffix.casefold()
    if extension not in SUPPORTED_EXTENSIONS:
        return _error(
            "unsupported_format",
            "Only .xlsx, .xlsm, and .pdf files are supported.",
            file=path.name,
            extension=extension,
        )

    max_rows = max(1, min(int(max_rows), MAX_ROW_LIMIT))
    max_pages = max(1, min(int(max_pages), MAX_PAGE_LIMIT))
    max_chars = max(5_000, min(int(max_chars), MAX_CHAR_LIMIT))
    sheet_name = sheet_name.strip() if sheet_name and sheet_name.strip() else None
    query = query.strip() if query and query.strip() else None

    try:
        if extension in {".xlsx", ".xlsm"}:
            if page_number is not None:
                return _error(
                    "page_not_applicable",
                    "page_number is only valid for PDF files.",
                    file=path.name,
                )
            return _extract_excel(
                path=path,
                sheet_name=sheet_name,
                query=query,
                max_rows=max_rows,
                max_chars=max_chars,
            )

        if sheet_name is not None:
            return _error(
                "sheet_not_applicable",
                "sheet_name is only valid for Excel files.",
                file=path.name,
            )
        return _extract_pdf(
            path=path,
            page_number=page_number,
            query=query,
            max_pages=max_pages,
            max_chars=max_chars,
        )
    except PermissionError:
        return _error(
            "file_locked",
            "The file is open or locked by another process. Close it and try again.",
            file=path.name,
        )
    except Exception as exc:  # Tool responses must remain structured and UI-safe.
        return _error(
            "extraction_failed",
            "The schedule file could not be extracted.",
            file=path.name,
            exception_type=type(exc).__name__,
            reason=str(exc),
        )
