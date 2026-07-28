export function toDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function fromDateKey(dateKey) {
  const [year, month, day] = dateKey.split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function isSameDay(first, second) {
  return toDateKey(first) === toDateKey(second);
}

export function isSameMonth(first, second) {
  return (
    first.getFullYear() === second.getFullYear() &&
    first.getMonth() === second.getMonth()
  );
}

export function addMonths(date, amount) {
  return new Date(date.getFullYear(), date.getMonth() + amount, 1);
}

export function getCalendarDays(displayDate) {
  const year = displayDate.getFullYear();
  const month = displayDate.getMonth();
  const firstOfMonth = new Date(year, month, 1);
  const mondayOffset = (firstOfMonth.getDay() + 6) % 7;
  const gridStart = new Date(year, month, 1 - mondayOffset);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const gridDayCount = Math.ceil((mondayOffset + daysInMonth) / 7) * 7;

  return Array.from({ length: gridDayCount }, (_, index) => {
    const date = new Date(
      gridStart.getFullYear(),
      gridStart.getMonth(),
      gridStart.getDate() + index,
    );
    return {
      date,
      dateKey: toDateKey(date),
      isCurrentMonth: isSameMonth(date, displayDate),
    };
  });
}

export function formatMonthYear(date) {
  return new Intl.DateTimeFormat("en", {
    month: "long",
    year: "numeric",
  }).format(date);
}

export function formatFullDate(date) {
  return new Intl.DateTimeFormat("en", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

export function formatDayTitle(date) {
  return new Intl.DateTimeFormat("en", {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(date);
}

export function formatTimestamp(date = new Date()) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function getEventCounts(events) {
  const counts = {
    Lecture: 0,
    Tutorial: 0,
    Quiz: 0,
    Exam: 0,
  };

  let cancelled = 0;
  for (const event of events) {
    if (event.status === "Cancelled") {
      cancelled += 1;
    } else {
      counts[event.type] += 1;
    }
  }

  return { counts, cancelled };
}
