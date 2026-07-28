"""Self-Healing University Scheduler agent."""

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from console_presenter import format_console_result
from intake_middleware import (
    answer_intake,
    format_console_question,
    parse_console_answer,
    start_intake,
)

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
from tools.cancel_day import set_cancel_day_progress_reporter


ENV_FILE = Path(__file__).with_name(".env")


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    minutes, second = divmod(total_seconds, 60)
    hours, minute = divmod(minutes, 60)
    return (
        f"{hours:02d}:{minute:02d}:{second:02d}"
        if hours
        else f"{minute:02d}:{second:02d}"
    )


class ConsoleProgressTimer:
    """Display elapsed time and a progress-based ETA for the console UI."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._phase_started = self._started
        self._phase = "Agent processing"
        self._completed: int | None = None
        self._total: int | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._last_width = 0

    def start(self) -> None:
        self._thread.start()

    def update(
        self,
        phase: str,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        with self._lock:
            if phase != self._phase:
                self._phase_started = time.perf_counter()
            self._phase = phase
            self._completed = completed
            self._total = total

    def _line(self) -> str:
        now = time.perf_counter()
        with self._lock:
            phase = self._phase
            completed = self._completed
            total = self._total
            phase_started = self._phase_started
        elapsed = now - self._started
        progress = ""
        remaining = "estimating..."
        if total is not None and completed is not None and total > 0:
            completed = min(max(completed, 0), total)
            progress = f" | progress {completed}/{total}"
            if completed > 0:
                phase_elapsed = max(0, now - phase_started)
                eta = (phase_elapsed / completed) * (total - completed)
                remaining = f"~{_format_duration(eta)}"
        return (
            f"[Working] {phase}{progress} | elapsed {_format_duration(elapsed)} "
            f"| remaining {remaining}"
        )

    def _render(self) -> None:
        line = self._line()
        padding = max(0, self._last_width - len(line))
        sys.stdout.write("\r" + line + (" " * padding))
        sys.stdout.flush()
        self._last_width = len(line)

    def _run(self) -> None:
        self._render()
        while not self._stop_event.wait(1):
            self._render()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)
        elapsed = time.perf_counter() - self._started
        clear_width = max(self._last_width, 1)
        sys.stdout.write("\r" + (" " * clear_width) + "\r")
        print(f"[Finished] Agent completed in {_format_duration(elapsed)}")


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
- Treat values selected in the website UI as confirmed, authoritative inputs.
- Ask a clarification question only when a mandatory tool input is missing or
  contradictory and cannot be derived directly from the supplied UI values.
- Ask only for the missing mandatory value. Do not request background details,
  explanations, dates, calendar labels, approvals, or policy choices that the
  selected operation does not require.
- Keep every request precise and concise. Do not present menus, speculative
  options, generic workflows, or "once confirmed" task lists unless the UI
  explicitly requests them.
- Retrieve every authoritative schedule source relevant to the current
  decision. Sources may include the General, Room, Doctors, and Exam schedules,
  plus enrollment, equipment, and other supporting schedules when relevant.
- Discover schedule sources from the available data directory. Use the
  authoritative files whose names begin with 01 through 07; do not require the
  user to provide or confirm exact file paths for those files.
- Treat a natural-language file reference as sufficient when exactly one
  available file matches it. Resolve the name with the tools and proceed
  without asking for the exact filename or path. Ask only when no file or more
  than one plausible file matches.
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

HOW TO USE CANCEL_DAY
- For a confirmed whole-day cancellation, call cancel_day as the parent
  orchestrator instead of manually coordinating hundreds of individual tool
  calls. Supply the exact academic week, reason, and authoritative general,
  staff, room, and exam schedule file names.
- A weekday plus an academic week is a complete time scope. Do not ask for a
  semester, academic year, or calendar date when those two values are present.
- The mandatory user inputs are the cancelled day, academic week, reason, and
  confirmation that the cancellation scope is approved. Schedule file names
  have authoritative defaults and must not be requested from the user.
- Treat an affirmative UI selection or direct confirmed cancellation command as
  cancellation_approved=true. This authorizes only the read-only prototype; it
  is not approval to apply a repaired timetable.
- If one mandatory value is absent, request only that value in one short
  sentence. Do not add examples, multiple-choice suggestions, or a workflow
  explanation.
- Set cancellation_approved=true only when the disruption itself is confirmed.
  This confirms the dry-run scope; it does not approve or apply the resulting
  compensation timetable.
- Use cancel_day only for a complete teaching-day cancellation. For a campus-wide
  partial-day or period cancellation, report partial_day_cancelled with the day,
  academic week, and exact start/end time. For a single cancelled class, report
  session_cancelled with its session ID instead.
- Keep maximum_following_weeks at 2 or less. Compensation can start on the next
  teaching day after the cancellation: use the remaining days of that academic
  week first, then at most the next two teaching weeks. Never place compensation
  on the cancelled day or an earlier day in the cancelled week.
- Never place a compensation session on a student group's normal day off. The
  selected weekday must already be a scheduled weekday for every tutorial or
  lecture group attached to that session; leave it unassigned if no such valid
  placement exists in the permitted window.
- Treat extreme_case.alerts as exceptional decision notices only. Near a
  midterm, major exam, or final, clearly identify the affected groups and state
  whether explicit authorization for day-off attendance or an additional
  official compensation day is required. Never claim that either exception was
  scheduled, approved, or applied by the tool.
- Follow prototype_timetable.pagination until has_more is false before claiming
  to have reviewed the complete proposal. cancel_day never writes to a source
  schedule.
- For a timetable UI, use prototype_timetable.day_views to render the available
  week/day tabs. Request a tab with display_academic_week and display_day, then
  render selected_day_schedule.sessions together. Distinguish rows using
  schedule_status: normal or compensation, apply the returned color_legend, and
  use slot_groups for the five period sections. Use display_period_id when the
  UI opens one slot, and follow the selected day/slot pagination.
- If prototype_complete is false, report the exact unassigned sessions and
  constraints after the orchestrator has attempted the complete confirmed scope.
- Report rejection totals as candidate-slot rejection counts, not as counts of
  sessions, rooms, staff members, or students.
- Use the tool's required_action exactly. Do not invent additional options such
  as extending beyond two weeks, reducing room capacity, substituting room
  types, splitting sessions, cancelling fewer sessions, or relaxing any hard
  constraint.

HOW TO USE RUN_SCHEDULE_REPAIR
- run_schedule_repair is a subordinate, side-effect-free transformation tool.
  It does not load files, call other tools, search alternatives, choose policy,
  validate, approve, or write a workbook.
- Call it only after every affected row and exactly one final assignment per
  affected session have been supplied by the parent workflow.
- Its result is one in-memory modified schedule. Do not describe that schedule
  as valid, approved, or applied until the exact candidate passes check_validity
  and receives explicit approval.

VALIDATION
- check_validity is the mandatory final gate.
- Every check_validity run must use its automatic source discovery so the
  requested schedule is checked with all authoritative 01-through-07 data.
  Explicit test fixtures may remain isolated from production schedules.
- In-memory repair options are proposals only. Materialize and compare an exact
  candidate schedule before final validation or approval.
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
    """Run the same deterministic intake workflow used by the future UI."""
    print("Self-Healing University Scheduler is ready. Type 'exit' or 'quit' to leave.")
    intake = start_intake()
    print(f"\nScheduler:\n{format_console_question(intake['question'])}\n")

    while True:
        user_message = input("You: ").strip()
        if user_message.lower() in {"exit", "quit"}:
            break
        if not user_message:
            continue

        try:
            answer = parse_console_answer(intake["question"], user_message)
            intake = answer_intake(intake["intake_id"], answer)
        except ValueError as exc:
            print(f"\nScheduler: {exc}")
            print(format_console_question(intake["question"]) + "\n")
            continue

        if intake["status"] == "collecting":
            print(f"\nScheduler:\n{format_console_question(intake['question'])}\n")
            continue
        if intake["status"] == "cancelled":
            print("\nScheduler: Request cancelled. No scheduling tool was run.\n")
            intake = start_intake()
            print(f"Scheduler:\n{format_console_question(intake['question'])}\n")
            continue

        timer = ConsoleProgressTimer()
        set_cancel_day_progress_reporter(timer.update)
        timer.start()
        try:
            from api import execute_ready_intake

            result = asyncio.run(execute_ready_intake(intake))
        finally:
            set_cancel_day_progress_reporter(None)
            timer.stop()
        print("\nScheduler:\n" + format_console_result(result) + "\n")
        intake = start_intake()
        print(f"Scheduler:\n{format_console_question(intake['question'])}\n")


if __name__ == "__main__":
    main()
