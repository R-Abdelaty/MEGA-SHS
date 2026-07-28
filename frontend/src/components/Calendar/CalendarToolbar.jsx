import Button from "../ui/Button";

export default function CalendarToolbar({
  monthLabel,
  onPreviousMonth,
  onNextMonth,
  onOpenInputs,
  onOpenChanges,
}) {
  return (
    <div className="calendar-toolbar">
      <div
        className="calendar-toolbar__navigation"
        aria-label="Calendar navigation"
      >
        <Button
          className="calendar-toolbar__month-button"
          onClick={onPreviousMonth}
          aria-label="Previous month"
        >
          <span aria-hidden="true">‹</span>
        </Button>
        <h1 className="calendar-toolbar__month" aria-live="polite">
          {monthLabel}
        </h1>
        <Button
          className="calendar-toolbar__month-button"
          onClick={onNextMonth}
          aria-label="Next month"
        >
          <span aria-hidden="true">›</span>
        </Button>
      </div>

      <div className="calendar-toolbar__mobile-actions">
        <Button onClick={onOpenInputs}>Schedule Inputs</Button>
        <Button onClick={onOpenChanges}>AI Changes</Button>
      </div>
    </div>
  );
}
