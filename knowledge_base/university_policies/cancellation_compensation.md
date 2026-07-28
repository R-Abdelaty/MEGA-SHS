# Cancellation and Compensation Procedure

Policy status: CONFIGURED from the existing scheduler prompt, with incomplete
fallback details.

## Confirmed rules

- When a university day is cancelled, every affected scheduled session requires
  compensation or an explicitly approved resolution.
- Before proposing compensation, check the relevant doctor/TA schedule, room
  availability, student conflicts, priorities, and required resources.
- Preserve unaffected timetable entries and make the smallest valid set of
  changes.
- Never schedule compensation on a student's normal day off. The selected day
  must already be a scheduled weekday for every student group attached to the
  affected session.
- If no valid compensation exists, report the blocking constraints instead of
  forcing a placement.
- Only in an extreme case near a midterm, major exam, or final, return a
  structured alert identifying the affected sessions and student groups. A
  student day-off remains prohibited unless the institution explicitly
  authorizes an exception; the prototype must not apply that exception itself.
- If no valid placement remains in the permitted window before the nearby major
  assessment, alert that the institution must designate an additional official
  compensation day. Do not create, approve, or apply that extra day automatically.

## Requires confirmation

- The existing policy does not define eligibility, capacity, attendance, or
  authorization for transferring students to another same-major group. Treat
  that fallback as unavailable; it cannot bypass the day-off prohibition.
  Cross-major tutorial/lab redistribution remains prohibited.
