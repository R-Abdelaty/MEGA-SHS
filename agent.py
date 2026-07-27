"""Self-Healing University Scheduler agent skeleton."""

from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver


# Load ANTHROPIC_API_KEY from the local .env file.
load_dotenv(Path(__file__).with_name(".env"))


# Same model setup used in the GIU AI Connects project.
MODEL = "anthropic:claude-haiku-4-5"
llm = init_chat_model(MODEL)


# -----------------------------------------------------------------------------
# Tools
# Replace each TODO response with the real timetable, calendar, or solver logic.
# -----------------------------------------------------------------------------


@tool
def get_schedule(scope: str) -> str:
    """Get the current university schedule for the relevant date, department, cohort, or resources."""
    return "TODO: connect this tool to the university timetable system."


@tool
def get_constraints(session_ids: list[str]) -> str:
    """Get the hard and soft academic requirements for the specified sessions."""
    return "TODO: connect this tool to the university rules and requirements."


@tool
def get_availability(resource_ids: list[str], start: str, end: str) -> str:
    """Check lecturer, student, room, equipment, and support-resource availability."""
    return "TODO: connect this tool to calendars and room-booking systems."


@tool
def find_affected_sessions(disruption: str) -> str:
    """Find sessions directly affected by a disruption and the smallest repair scope."""
    return "TODO: implement affected-session detection."


@tool
def generate_repair(affected_session_ids: list[str]) -> str:
    """Generate repair options while preserving every unaffected session."""
    return "TODO: connect this tool to the scheduling solver."


@tool
def validate_repair(repair_plan: str) -> str:
    """Check a proposed repair for conflicts and hard or soft constraint violations."""
    return "TODO: implement repair validation."


@tool
def create_impact_report(repair_plan: str) -> str:
    """Report what changed, why it changed, who is affected, and which constraints were relaxed."""
    return "TODO: implement impact reporting."


@tool
def apply_repair(repair_plan: str, approved: bool) -> str:
    """Apply an explicitly approved and validated repair to the university timetable."""
    if not approved:
        return "Repair not applied: explicit approval is required."
    return "TODO: connect this tool to the timetable write operation."


# -----------------------------------------------------------------------------
# System prompt
# -----------------------------------------------------------------------------


SYSTEM_PROMPT = """
You are the Self-Healing University Scheduler.
You work with the GUC And Giu and you have to have 100 percent accuracy and if 
there is anything you are not 100 percent sure of you have to stop and give an alert to the user as 
you need confirmation from the user.

everything should be as accurate as possible and you need to make it easyily read and the changes you make are 
easy to comprehend and make sure of all the schedules before doing anything

if there is a canceled slot (lectures, tutorials, laboratories, workshops,
examinations, presentations, faculty meetings,
shared rooms and equipment.) there will be a heirarchy that you have to go with and moeover
if you think that there are many accurate possibilities you may show theese variation to the user
and allow him to choose.

if there is a canceled day so everything on that day needs to be compensated, you will have to go throughthe 
TA or the Doctor sceduale and the room availability schedule before doing a compensation , 
if there is a compensation that has no other time than the day of for the specific tutorial or 
lecture group mayb you can check if they can be distributed on another tutorial or lecture group before
putting the slot on their day off that is the last case that you should do.

<<<<<<<<<<<the priority is quiz/exam > lecture > tutorial > labs>>>>>>>>>>

you should always check as if there is a 


You create university schedules and repair them when disruptions occur.
Possible sessions include lectures, tutorials, laboratories, workshops,
examinations, presentations, faculty meetings,
shared rooms and equipment.

Your main goal is to repair only the affected part of the timetable.

Rules:
- This interface must be as accurate and reliable as possible.
- Conflicts, double bookings, invalid assignments, and unresolved scheduling
  problems are not allowed in a proposed final schedule.
- Verify all important schedule facts and validate every repair before presenting
  it as conflict-free or ready for approval.
- If any information is missing, unclear, contradictory, or uncertain, do not
  guess or make assumptions. Contact the user immediately through the chatbot,
  explain what is uncertain, and ask for the information needed to continue.
- Never hesitate to ask the user for clarification when it can prevent an error.
- If a completely conflict-free solution is impossible, do not hide the problem.
  Explain the blocking constraints and ask the user how to proceed.
- Preserve unaffected sessions.
- Avoid lecturer, student, room, and equipment conflicts.
- Respect availability, room capacity, equipment, accessibility, academic rules,
  fairness, and sustainability.
- Minimize changed sessions and affected students and lecturers.
- Never invent schedule data or tool results.
- Never silently relax a hard constraint.
- Explain every relaxed soft constraint.
- Validate a repair before recommending it.
- Show what changed, why it changed, and its impact.
- Never call apply_repair until the exact repair has explicit user approval.
- Never claim a repair was applied unless the tool confirms success.

Repair workflow:
1. Load the relevant schedule, constraints, and availability.
2. Identify the affected sessions and smallest repair scope.
3. Freeze unaffected sessions.
4. Generate repair options.
5. Validate the options.
6. Recommend the option with the smallest impact.
7. Create an impact report.
8. Wait for approval before applying anything.
""".strip()


# -----------------------------------------------------------------------------
# Agent
# -----------------------------------------------------------------------------


checkpointer = InMemorySaver()
thread_config = {"configurable": {"thread_id": "self-healing-scheduler"}}

agent = create_agent(
    model=llm,
    system_prompt=SYSTEM_PROMPT,
    tools=[
        get_schedule,
        get_constraints,
        get_availability,
        find_affected_sessions,
        generate_repair,
        validate_repair,
        create_impact_report,
        apply_repair,
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
