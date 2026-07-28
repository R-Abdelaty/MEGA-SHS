const weekdays = [
  ["Mon", "Monday"],
  ["Tue", "Tuesday"],
  ["Wed", "Wednesday"],
  ["Thu", "Thursday"],
  ["Fri", "Friday"],
  ["Sat", "Saturday"],
  ["Sun", "Sunday"],
];

export default function CalendarHeader() {
  return (
    <div className="calendar__weekday-row" role="row">
      {weekdays.map(([shortName, fullName]) => (
        <div
          className="calendar__weekday"
          role="columnheader"
          aria-label={fullName}
          key={fullName}
        >
          {shortName}
        </div>
      ))}
    </div>
  );
}
