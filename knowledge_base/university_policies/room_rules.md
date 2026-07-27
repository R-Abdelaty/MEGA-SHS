# Room Assignment Rules

Policy status: CONFIGURED from the existing scheduler prompt and validator.

## Confirmed rules

- A room cannot host overlapping sessions.
- A room must be available for the entire assigned period.
- Expected attendance must not exceed the combined capacity of the assigned
  rooms.
- Room facts, availability, type, and capacity must come from authoritative
  schedule data; never infer them from a room name.
- A room change must be revalidated against students, instructors, exams,
  equipment, and all other affected schedules.

## Requires confirmation

- No additional institution-specific room-type suitability matrix was supplied.
  Retrieve equipment/accessibility policy and request confirmation when room
  suitability cannot be proven from available data.
