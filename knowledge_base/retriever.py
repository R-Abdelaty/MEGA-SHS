"""Deterministic, category-scoped retrieval for university scheduling policies."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal, cast


KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parent
POLICY_DIR = (KNOWLEDGE_BASE_DIR / "university_policies").resolve()
INDEX_PATH = POLICY_DIR / "index.json"
DETERMINISTIC_RULES_PATH = POLICY_DIR / "deterministic_rules.json"
MAX_CATEGORIES_PER_REQUEST = 4

PolicyCategory = Literal[
    "session_priorities",
    "course_sharing",
    "cohort_rules",
    "cancellation_compensation",
    "room_rules",
    "equipment_rules",
    "exam_rules",
    "instructor_rules",
    "student_conflict_rules",
    "fairness_accessibility",
    "university_procedures",
]

POLICY_CATEGORIES: tuple[str, ...] = (
    "session_priorities",
    "course_sharing",
    "cohort_rules",
    "cancellation_compensation",
    "room_rules",
    "equipment_rules",
    "exam_rules",
    "instructor_rules",
    "student_conflict_rules",
    "fairness_accessibility",
    "university_procedures",
)


class PolicyKnowledgeError(ValueError):
    """Raised when policy content is missing, malformed, or requested unsafely."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyKnowledgeError(f"Policy file is missing: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyKnowledgeError(
            f"Policy file contains invalid JSON: {path.name}"
        ) from exc
    if not isinstance(payload, dict):
        raise PolicyKnowledgeError(f"Policy file must contain an object: {path.name}")
    return payload


@lru_cache(maxsize=1)
def _policy_index() -> dict[str, dict[str, Any]]:
    payload = _read_json(INDEX_PATH)
    entries = payload.get("categories")
    if not isinstance(entries, list):
        raise PolicyKnowledgeError("Policy index must contain a categories list.")

    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PolicyKnowledgeError("Each policy index entry must be an object.")
        category = str(entry.get("category", "")).strip()
        relative_file = str(entry.get("file", "")).strip()
        if category not in POLICY_CATEGORIES or not relative_file:
            raise PolicyKnowledgeError(
                f"Policy index contains an invalid category entry: {category!r}."
            )
        document_path = (POLICY_DIR / relative_file).resolve()
        if POLICY_DIR not in document_path.parents:
            raise PolicyKnowledgeError(
                f"Policy document escapes the knowledge-base directory: {relative_file}"
            )
        if not document_path.is_file():
            raise PolicyKnowledgeError(
                f"Policy document is missing for {category}: {relative_file}"
            )
        index[category] = {**entry, "path": document_path}

    missing = set(POLICY_CATEGORIES) - set(index)
    if missing:
        raise PolicyKnowledgeError(
            f"Policy index is missing categories: {', '.join(sorted(missing))}."
        )
    return index


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", query.casefold())
        if len(token) > 2
    }


def _rank_categories(query: str) -> list[str]:
    tokens = _query_tokens(query)
    if not tokens:
        return []
    scored: list[tuple[int, str]] = []
    for category, entry in _policy_index().items():
        keywords = {
            str(keyword).casefold()
            for keyword in entry.get("keywords", [])
            if str(keyword).strip()
        }
        category_tokens = set(category.split("_"))
        score = len(tokens.intersection(keywords)) * 3
        score += len(tokens.intersection(category_tokens))
        if score:
            scored.append((score, category))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [category for _, category in scored[:3]]


def retrieve_policies(
    categories: Iterable[str] | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Retrieve a small set of policy documents by category or problem text."""
    selected: list[str] = []
    for category in categories or []:
        normalized = str(category).strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized and normalized not in selected:
            selected.append(normalized)

    unknown = [category for category in selected if category not in POLICY_CATEGORIES]
    if unknown:
        raise PolicyKnowledgeError(
            "Unknown policy categories: "
            f"{', '.join(unknown)}. Available categories: "
            f"{', '.join(POLICY_CATEGORIES)}."
        )
    if len(selected) > MAX_CATEGORIES_PER_REQUEST:
        raise PolicyKnowledgeError(
            f"Retrieve at most {MAX_CATEGORIES_PER_REQUEST} relevant policy "
            "categories per call."
        )

    cleaned_query = str(query or "").strip()
    if not selected and cleaned_query:
        selected = _rank_categories(cleaned_query)
    if not selected:
        raise PolicyKnowledgeError(
            "Provide one or more relevant categories or a scheduling-problem query."
        )

    index = _policy_index()
    documents = []
    for category in selected:
        entry = index[category]
        content = cast(Path, entry["path"]).read_text(encoding="utf-8").strip()
        documents.append(
            {
                "category": category,
                "title": entry["title"],
                "policy_status": entry["policy_status"],
                "content": content,
            }
        )

    return {
        "status": "ok",
        "context_type": "authoritative_university_policy",
        "categories": selected,
        "documents": documents,
        "usage": (
            "Apply confirmed rules as authoritative constraints. Any item marked "
            "REQUIRES CONFIRMATION is not a usable policy until the user or an "
            "authorized policy source confirms it."
        ),
    }


@lru_cache(maxsize=1)
def load_deterministic_rules() -> dict[str, Any]:
    """Load machine-readable rules enforced by deterministic validation."""
    payload = _read_json(DETERMINISTIC_RULES_PATH)
    rules = payload.get("rules")
    if not isinstance(rules, dict):
        raise PolicyKnowledgeError(
            "deterministic_rules.json must contain a rules object."
        )
    required = {
        "teaching_weeks",
        "final_exam_start_times",
        "preferred_quiz_periods",
        "quiz_period_rule_is_hard",
    }
    missing = required - set(rules)
    if missing:
        raise PolicyKnowledgeError(
            "Deterministic policy rules are missing: "
            f"{', '.join(sorted(missing))}."
        )
    # Return a detached JSON-compatible copy so callers cannot mutate the cache.
    return cast(dict[str, Any], json.loads(json.dumps(rules)))
