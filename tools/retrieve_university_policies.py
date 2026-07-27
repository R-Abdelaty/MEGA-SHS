"""Targeted retrieval tool for GUC/GIU scheduling policy context."""

from __future__ import annotations

import json

from langchain.tools import tool

from knowledge_base.retriever import (
    PolicyCategory,
    PolicyKnowledgeError,
    retrieve_policies,
)


@tool
def retrieve_university_policies(
    categories: list[PolicyCategory] | None = None,
    query: str | None = None,
) -> str:
    """Retrieve only the university-policy categories relevant to a decision.

    Use this before applying GUC/GIU-specific policy. Prefer explicit categories;
    use ``query`` when the correct category is uncertain. Retrieve at most four
    categories per call rather than loading the entire knowledge base.

    Routing examples:
    - Cancelled tutorial: ``cancellation_compensation``, ``cohort_rules``,
      ``student_conflict_rules``.
    - Room change: ``room_rules``, ``equipment_rules``,
      ``fairness_accessibility`` when accessibility matters.
    - Exam conflict: ``exam_rules``, ``session_priorities``.
    - Shared lecture: ``course_sharing``, ``cohort_rules``.

    Returned CONFIGURED rules are authoritative policy context. Text marked
    REQUIRES CONFIRMATION is a known policy gap, not permission to guess; request
    confirmation before relying on it. Call again with another small category
    set only when the scheduling problem genuinely requires it.
    """
    try:
        payload = retrieve_policies(categories=categories, query=query)
    except PolicyKnowledgeError as exc:
        payload = {
            "status": "error",
            "error": {
                "code": "policy_retrieval_failed",
                "message": str(exc),
            },
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)
