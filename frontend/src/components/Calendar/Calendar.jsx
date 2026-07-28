import CalendarCell from "./CalendarCell";
import CalendarHeader from "./CalendarHeader";
import CalendarToolbar from "./CalendarToolbar";
import {
  formatMonthYear,
  getCalendarDays,
  isSameDay,
} from "../../utils/calendarUtils";
import "./Calendar.css";

export default function Calendar({
  displayDate,
  schedule,
  onSelectDate,
  onPreviousMonth,
  onNextMonth,
  onOpenInputs,
  onOpenChanges,
}) {
  const today = new Date();
  const calendarDays = getCalendarDays(displayDate);

  return (
    <section
      className="calendar-section"
      aria-label="Monthly university schedule"
    >
      <CalendarToolbar
        monthLabel={formatMonthYear(displayDate)}
        onPreviousMonth={onPreviousMonth}
        onNextMonth={onNextMonth}
        onOpenInputs={onOpenInputs}
        onOpenChanges={onOpenChanges}
      />
      <div className="calendar-panel">
        <div className="calendar" role="grid">
          <CalendarHeader />
          <div
            className="calendar__grid"
            role="rowgroup"
            style={{ "--calendar-row-count": calendarDays.length / 7 }}
          >
            {calendarDays.map((day) => (
              <CalendarCell
                key={day.dateKey}
                date={day.date}
                events={schedule[day.dateKey] ?? []}
                isCurrentMonth={day.isCurrentMonth}
                isToday={isSameDay(day.date, today)}
                onSelect={onSelectDate}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
