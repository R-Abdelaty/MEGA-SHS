function formatPosition(position) {
  if (!position) return "Unavailable";
  const parsed = new Date(`${position.date}T00:00:00`);
  const dateLabel = Number.isNaN(parsed.getTime())
    ? position.date
    : new Intl.DateTimeFormat("en", {
        weekday: "long",
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(parsed);
  return `${dateLabel} · ${position.start_time}–${position.end_time}`;
}

function getRoomChange(action) {
  const previousRoom = action.previous?.room || action.event.room;
  const proposedRoom = action.proposed?.room || previousRoom;
  return {
    changed:
      previousRoom.localeCompare(proposedRoom, undefined, {
        sensitivity: "accent",
      }) !== 0,
    previousRoom,
    proposedRoom,
  };
}

function CancellationSummary({ cancellation }) {
  if (!cancellation) return null;
  return (
    <section className="healing-review__cancellation">
      <h3>Requested cancellation</h3>
      <p>
        {cancellation.cancellation_type === "day"
          ? `Entire day · ${cancellation.date}`
          : `${cancellation.event_ids.length} selected ${
              cancellation.event_ids.length === 1 ? "activity" : "activities"
            }`}
      </p>
    </section>
  );
}

export default function HealingRunReview({ run, error, isSubmitting }) {
  if (isSubmitting && !run) {
    return (
      <div className="healing-review__state" role="status">
        Creating the healing run…
      </div>
    );
  }
  if (!run) {
    return error ? (
      <div className="healing-review__state healing-review__state--error" role="alert">
        <strong>Could not create the healing run</strong>
        <span>{error.message}</span>
      </div>
    ) : null;
  }

  if (run.status === "processing") {
    return (
      <div className="healing-review__state" role="status">
        <span className="healing-review__spinner" aria-hidden="true" />
        <strong>Healing the schedule</strong>
        <span>
          The scheduling agent is checking the relevant schedules and
          constraints.
        </span>
        {error ? <span className="healing-review__error">{error.message}</span> : null}
      </div>
    );
  }

  if (run.status === "failed" || run.status === "stale") {
    return (
      <div
        className="healing-review__state healing-review__state--error"
        role="alert"
      >
        <strong>
          {run.status === "stale"
            ? "This proposal is stale"
            : "The healing run failed"}
        </strong>
        <span>
          {run.error?.message ||
            error?.message ||
            "Create a new healing run and try again."}
        </span>
      </div>
    );
  }

  if (run.status === "approved" || run.status === "rejected") {
    return (
      <div className="healing-review__state" role="status">
        <strong>
          {run.status === "approved"
            ? "Schedule preview updated"
            : "Healing run rejected"}
        </strong>
        <span>
          {run.status === "approved"
            ? "The cancellation and all approved movements are now visible in the calendar."
            : "The calendar preview was left unchanged."}
        </span>
      </div>
    );
  }

  return (
    <div className="healing-review">
      <CancellationSummary cancellation={run.requested_cancellation} />
      <section className="healing-review__summary">
        <h3>Agent summary</h3>
        <p>{run.summary}</p>
      </section>
      <section className="healing-review__moves">
        <h3>Proposed movements ({run.proposed_actions.length})</h3>
        {run.proposed_actions.length === 0 ? (
          <p className="healing-review__empty">
            No additional schedule movements are required.
          </p>
        ) : (
          <ul>
            {run.proposed_actions.map((action) => {
              const room = getRoomChange(action);
              return (
                <li key={action.action_id}>
                  <div className="healing-review__move-heading">
                    <strong>{action.event.name}</strong>
                    <span>{action.event.type}</span>
                  </div>
                  <dl>
                    <div>
                      <dt>{room.changed ? "Room change" : "Room"}</dt>
                      <dd>
                        {room.changed
                          ? `${room.previousRoom} → ${room.proposedRoom}`
                          : room.proposedRoom}
                      </dd>
                    </div>
                    <div>
                      <dt>Student group</dt>
                      <dd>{action.event.student_group}</dd>
                    </div>
                    <div>
                      <dt>Previous</dt>
                      <dd>{formatPosition(action.previous)}</dd>
                    </div>
                    <div>
                      <dt>Proposed</dt>
                      <dd>{formatPosition(action.proposed)}</dd>
                    </div>
                  </dl>
                  {action.reason ? (
                    <p className="healing-review__reason">{action.reason}</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>
      {error ? (
        <p className="healing-review__error" role="alert">
          {error.message}
        </p>
      ) : null}
    </div>
  );
}
