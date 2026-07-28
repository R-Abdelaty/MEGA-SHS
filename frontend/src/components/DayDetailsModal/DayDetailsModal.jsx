import { useEffect, useState } from "react";
import Button from "../ui/Button";
import DayAgenda from "./DayAgenda";
import HealingRunReview from "./HealingRunReview";
import { formatDayTitle } from "../../utils/calendarUtils";
import "./DayDetailsModal.css";

const CLOSE_ANIMATION_MS = 310;

function eventMatchesSearch(event, normalizedSearch) {
  return [
    event.name,
    event.type,
    event.room,
    event.studentGroup,
    event.startTime,
    event.endTime,
    event.status,
  ].some((value) =>
    String(value ?? "")
      .toLocaleLowerCase()
      .includes(normalizedSearch),
  );
}

export default function DayDetailsModal({
  date,
  events,
  origin,
  healingRun,
  healingError,
  isSubmitting,
  isResolving,
  onClose,
  onCancelEvents,
  onCancelDay,
  onApprove,
  onReject,
}) {
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [isClosing, setIsClosing] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    setSelectedIds(new Set());
    setIsClosing(false);
    setSearchQuery("");
  }, [date]);

  useEffect(() => {
    if (!date) return undefined;

    function handleEscape(event) {
      if (
        event.key === "Escape" &&
        healingRun?.status !== "processing" &&
        !isSubmitting &&
        !isResolving
      ) {
        event.preventDefault();
        setIsClosing(true);
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [date, healingRun?.status, isResolving, isSubmitting]);

  useEffect(() => {
    if (!isClosing) return undefined;

    const respectsReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const timerId = window.setTimeout(
      onClose,
      respectsReducedMotion ? 0 : CLOSE_ANIMATION_MS,
    );

    return () => window.clearTimeout(timerId);
  }, [isClosing, onClose]);

  if (!date) return null;

  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const estimatedModalWidth =
    viewportWidth <= 760
      ? viewportWidth - 20
      : Math.min(640, viewportWidth - 40);
  const estimatedModalHeight =
    viewportWidth <= 760
      ? Math.min(720, viewportHeight - 20)
      : Math.min(720, viewportHeight - 48);
  const modalAnimationStyle = origin
    ? {
        "--day-modal-origin-x": `${origin.centerX - viewportWidth / 2}px`,
        "--day-modal-origin-y": `${origin.centerY - viewportHeight / 2}px`,
        "--day-modal-scale-x": Math.max(
          0.08,
          origin.width / estimatedModalWidth,
        ),
        "--day-modal-scale-y": Math.max(
          0.08,
          origin.height / estimatedModalHeight,
        ),
      }
    : undefined;

  const activeEvents = events.filter((event) => event.status === "Active");
  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();
  const filteredEvents = normalizedSearch
    ? events.filter((event) => eventMatchesSearch(event, normalizedSearch))
    : events;
  const isReviewing = Boolean(healingRun || isSubmitting || healingError);
  const isBusy =
    isSubmitting || isResolving || healingRun?.status === "processing";
  const allActiveEventsSelected =
    activeEvents.length > 0 &&
    activeEvents.every((event) => selectedIds.has(event.id));

  function toggleEvent(eventId) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      return next;
    });
  }

  async function cancelSelected() {
    if (selectedIds.size === 0) return;
    const started = await onCancelEvents([...selectedIds]);
    if (started) setSelectedIds(new Set());
  }

  function selectAllEvents() {
    setSelectedIds(new Set(activeEvents.map((event) => event.id)));
  }

  function requestClose() {
    if (!isClosing && !isBusy) setIsClosing(true);
  }

  return (
    <div
      className={`modal-layer ${isClosing ? "modal-layer--closing" : ""}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <section
        className={`day-modal ${isClosing ? "day-modal--closing" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="day-modal-title"
        style={modalAnimationStyle}
      >
        <div className="day-modal__header">
          <Button
            className="day-modal__select-all"
            onClick={selectAllEvents}
            disabled={
              isReviewing ||
              activeEvents.length === 0 ||
              allActiveEventsSelected
            }
          >
            {isReviewing ? "Healing run" : "Select All"}
          </Button>
          <h2 id="day-modal-title">{formatDayTitle(date)}</h2>
          <Button
            className="day-modal__close"
            type="button"
            onClick={requestClose}
            disabled={isBusy}
            aria-label="Close day details"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              width="22"
              height="22"
              fill="none"
            >
              <path
                d="M5 5L19 19M19 5L5 19"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </Button>
        </div>

        {!isReviewing && events.length > 0 ? (
          <label className="day-modal__search">
            <span className="sr-only">Search schedule items</span>
            <input
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search this day"
              autoComplete="off"
            />
          </label>
        ) : null}

        <div className="day-modal__body">
          {isReviewing ? (
            <HealingRunReview
              run={healingRun}
              error={healingError}
              isSubmitting={isSubmitting}
            />
          ) : events.length === 0 ? (
            <div className="day-modal__empty">
              No lectures, tutorials, quizzes, or exams are scheduled.
            </div>
          ) : filteredEvents.length === 0 ? (
            <div className="day-modal__empty" role="status">
              No schedule items match “{searchQuery.trim()}”.
            </div>
          ) : (
            <DayAgenda
              events={filteredEvents}
              selectedIds={selectedIds}
              onToggle={toggleEvent}
            />
          )}
        </div>

        <div className="day-modal__footer">
          {!isReviewing ? (
            <>
              <Button
                type="button"
                onClick={onCancelDay}
                disabled={activeEvents.length === 0}
              >
                Cancel Entire Day
              </Button>
              <Button
                className="day-modal__cancel-selected"
                onClick={cancelSelected}
                disabled={selectedIds.size === 0}
                variant="accent"
              >
                Cancel selected
                {selectedIds.size > 0 ? ` (${selectedIds.size})` : ""}
              </Button>
            </>
          ) : null}
          {healingRun?.status === "approval_required" ? (
            <>
              <Button
                type="button"
                onClick={onReject}
                disabled={isResolving}
              >
                {isResolving ? "Working…" : "Reject"}
              </Button>
              <Button
                type="button"
                variant="accent"
                onClick={onApprove}
                disabled={isResolving}
              >
                {isResolving ? "Applying…" : "Approve Entire Run"}
              </Button>
            </>
          ) : null}
          {isReviewing &&
          healingRun?.status !== "processing" &&
          healingRun?.status !== "approval_required" &&
          !isSubmitting ? (
            <Button type="button" onClick={requestClose}>
              Close
            </Button>
          ) : null}
        </div>
      </section>
    </div>
  );
}
