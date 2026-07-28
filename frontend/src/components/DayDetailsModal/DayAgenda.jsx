function groupEventsByPeriod(events) {
  const sortedEvents = [...events].sort((first, second) => {
    const startComparison = first.startTime.localeCompare(second.startTime);
    if (startComparison !== 0) return startComparison;
    return first.endTime.localeCompare(second.endTime);
  });

  const periods = new Map();
  for (const event of sortedEvents) {
    const periodKey = `${event.startTime}-${event.endTime}`;
    const period = periods.get(periodKey);

    if (period) {
      period.events.push(event);
    } else {
      periods.set(periodKey, {
        id: periodKey,
        startTime: event.startTime,
        endTime: event.endTime,
        events: [event],
      });
    }
  }

  return [...periods.values()];
}

function AgendaEvent({ event, isSelected, onToggle }) {
  const isCancelled = event.status === "Cancelled";

  return (
    <li
      className={[
        "agenda-event",
        `agenda-event--${event.type.toLowerCase()}`,
        isCancelled ? "agenda-event--cancelled" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <input
        type="checkbox"
        checked={isSelected}
        disabled={isCancelled}
        onChange={() => onToggle(event.id)}
        aria-label={`Select ${event.name} ${event.type}`}
      />
      <div className="agenda-event__content">
        <div className="agenda-event__heading">
          <strong>{event.name}</strong>
          <span>{event.type}</span>
        </div>
        <div className="agenda-event__details">
          <span>Room {event.room}</span>
          <span>{event.studentGroup}</span>
        </div>
        {isCancelled ? (
          <span className="agenda-event__status">Cancelled</span>
        ) : null}
      </div>
    </li>
  );
}

export default function DayAgenda({ events, selectedIds, onToggle }) {
  const periods = groupEventsByPeriod(events);

  return (
    <div className="day-agenda" aria-label="Schedule by time">
      {periods.map((period) => (
        <section
          className="agenda-period"
          aria-label={`${period.startTime} to ${period.endTime}`}
          key={period.id}
        >
          <div className="agenda-period__time" aria-hidden="true">
            <strong>{period.startTime}</strong>
            <span>{period.endTime}</span>
          </div>
          <ul className="agenda-period__events">
            {period.events.map((event) => (
              <AgendaEvent
                event={event}
                isSelected={selectedIds.has(event.id)}
                onToggle={onToggle}
                key={event.id}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
