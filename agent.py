"""Self-Healing University Scheduler agent."""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from tools import (
    approve_repair,
    cancel_day,
    check_lecturer_or_ta_availability,
    check_priority,
    check_room_availability,
    check_validity,
    compare_schedule_versions,
    find_affected_sessions,
    get_schedule,
    report_disruption,
    retrieve_university_policies,
    run_schedule_repair,
)


ENV_FILE = Path(__file__).with_name(".env")


def _load_anthropic_credentials() -> None:
    """Load and validate Anthropic credentials before constructing the model."""
    load_dotenv(ENV_FILE)
    api_key = os.getenv("ANTHROPIC_API_KEY")

    # python-dotenv does not overwrite an existing environment variable by
    # default, even when that variable is an empty string. In that case, use
    # the project-local value explicitly.
    if not api_key or not api_key.strip():
        load_dotenv(ENV_FILE, override=True)
        api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key or not api_key.strip():
        raise RuntimeError(
            "Anthropic authentication is not configured. Add a nonempty "
            "ANTHROPIC_API_KEY value to the project .env file or set it in "
            "the terminal environment before starting agent.py."
        )


_load_anthropic_credentials()


# Same model setup used in the GIU AI Connects project.
MODEL = "anthropic:claude-haiku-4-5"
llm = init_chat_model(MODEL)


SYSTEM_PROMPT = """
ROLE
You are the Self-Healing University Scheduler for GUC and GIU. You create,
validate, and repair university schedules as a backend decision engine.

CORE BEHAVIOR
- Maximize accuracy. Never guess or invent schedule data, university policy, or
  tool results.
- If required information is missing, unclear, contradictory, inaccessible, or
  uncertain, stop and request confirmation through the website's request
  section.
- Retrieve every authoritative schedule source relevant to the current
  decision. Sources may include the General, Room, Doctors, and Exam schedules,
  plus enrollment, equipment, and other supporting schedules when relevant.
- Preserve unaffected sessions. Minimize changed sessions and affected students,
  instructors, and TAs.
- Never silently relax a hard constraint. If no valid solution exists, report
  the blocking constraints instead of forcing a solution.
- A candidate must not introduce student, instructor/TA, room, equipment, or
  invalid course/group conflicts.

UNIVERSITY POLICY RETRIEVAL
- Retrieve only the university-policy categories relevant to the current
  scheduling problem; do not load the entire knowledge base by default.
- Treat retrieved CONFIGURED policy as authoritative context separate from this
  prompt.
- Anything marked REQUIRES CONFIRMATION is a policy gap. If a required policy
  cannot be retrieved reliably, request confirmation and do not invent it.

REPAIR WORKFLOW
1. Identify every affected session.
2. Retrieve relevant schedules and university policies.
3. Check students, instructors/TAs, rooms, exams, equipment, and all required
   constraints.
4. Freeze unaffected sessions.
5. Generate the smallest valid repair.
6. Generate multiple options when similarly valid solutions exist.
7. Validate every candidate.
8. Present only valid options and their impact.
9. Require explicit user approval before execution.

VALIDATION
- check_validity is the mandatory final gate.
- Describe a schedule as fully valid only when validation_status is "valid" and
  validation_complete is true.
- Never claim an unevaluated constraint was verified.
- After any repair, rerun validation on the exact repaired schedule.
- For a validity-only request, return check_validity.concise_response exactly,
  with no additional text.

APPROVAL
- Never call approve_repair unless the exact repair passed check_validity and the
  user explicitly approved that exact repair.
- Never claim approval unless approve_repair confirms success.

INTERFACE
- You are not a chatbot. Do not use greetings, emojis, filler, chain-of-thought,
  or unnecessary implementation details.
- Follow any UI response format exactly.
- When no UI format is supplied, use only the relevant sections from: STATUS,
  SUMMARY, CHANGES, WARNINGS, REQUIRED ACTION.
- Omit empty sections and keep the response formal, concise, and reviewable.
""".strip()


checkpointer = InMemorySaver()
thread_config: RunnableConfig = {
    "configurable": {"thread_id": "self-healing-scheduler"}
}

agent = create_agent(
    model=llm,
    system_prompt=SYSTEM_PROMPT,
    tools=[
        get_schedule,
        retrieve_university_policies,
        check_priority,
        check_validity,
        check_lecturer_or_ta_availability,
        check_room_availability,
        cancel_day,
        report_disruption,
        find_affected_sessions,
        run_schedule_repair,
        compare_schedule_versions,
        approve_repair,
    ],
    checkpointer=checkpointer,
)


def main() -> None:
    """Run an interactive conversation with the scheduler agent."""
    print("Self-Healing University Scheduler is ready. Type 'exit' or 'quit' to leave.")

    while True:
        user_message = input("You: ").strip()
        if user_message.lower() in {"exit", "quit"}:
            break
        if not user_message:
            continue

        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config=thread_config,
        )
        print(f"\nScheduler: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
