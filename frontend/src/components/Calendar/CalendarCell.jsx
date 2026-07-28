import { formatFullDate, getEventCounts } from "../../utils/calendarUtils";

const visualCountLabels = {
  Lecture: ["Lec", "Lec"],
  Tutorial: ["Tut", "Tut"],
  Quiz: ["Quiz", "Quizzes"],
  Exam: ["Exam", "Exams"],
};

const accessibleCountLabels = {
  Lecture: ["Lecture", "Lectures"],
  Tutorial: ["Tutorial", "Tutorials"],
  Quiz: ["Quiz", "Quizzes"],
  Exam: ["Exam", "Exams"],
};

export default function CalendarCell({
  date,
  events,
  isCurrentMonth,
  isToday,
  onSelect,
}) {
  const { counts, cancelled } = getEventCounts(events);
  const entireDayCancelled =
    events.length > 0 && events.every((event) => event.status === "Cancelled");

  function openDayDetails(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    onSelect(date, {
      centerX: rect.left + rect.width / 2,
      centerY: rect.top + rect.height / 2,
      height: rect.height,
      width: rect.width,
    });
  }

  return (
    <button
      type="button"
      className={[
        "calendar-cell",
        isCurrentMonth ? "" : "calendar-cell--muted",
        isToday ? "calendar-cell--today" : "",
        entireDayCancelled ? "calendar-cell--cancelled" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={openDayDetails}
      disabled={!isCurrentMonth}
      aria-label={`View schedule for ${formatFullDate(date)}${
        entireDayCancelled ? ", entire day cancelled" : ""
      }`}
    >
      <span className="calendar-cell__topline">
        <span className="calendar-cell__day">{date.getDate()}</span>
      </span>
      <span className="calendar-cell__counts">
        {Object.entries(counts).map(([type, count]) =>
          count > 0 ? (
            <span
              className={`calendar-cell__count calendar-cell__count--${type.toLowerCase()}`}
              aria-label={`${count} ${
                accessibleCountLabels[type][count === 1 ? 0 : 1]
              }`}
              key={type}
            >
              <span className="calendar-cell__count-number">{count}</span>{" "}
              <span
                className={
                  type === "Lecture" ||
                  type === "Tutorial" ||
                  type === "Quiz"
                    ? "calendar-cell__count-label--strong"
                    : undefined
                }
                aria-hidden="true"
              >
                {visualCountLabels[type][count === 1 ? 0 : 1]}
              </span>
            </span>
          ) : null,
        )}
        {cancelled > 0 && !entireDayCancelled ? (
          <span className="calendar-cell__cancelled-count">
            {cancelled} cancelled
          </span>
        ) : null}
        {entireDayCancelled ? (
          <span className="calendar-cell__cancelled-label">Day cancelled</span>
        ) : null}
      </span>
    </button>
  );
}
