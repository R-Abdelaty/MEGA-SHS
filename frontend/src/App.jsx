import { useCallback, useEffect, useRef, useState } from "react";
import Calendar from "./components/Calendar/Calendar";
import ChangeSummaryPanel from "./components/ChangeSummaryPanel/ChangeSummaryPanel";
import DayDetailsModal from "./components/DayDetailsModal/DayDetailsModal";
import FileUploadPanel from "./components/FileUploadPanel/FileUploadPanel";
import MobileDrawer from "./components/MobileDrawer/MobileDrawer";
import useHealingRun from "./hooks/useHealingRun";
import { getChangeHistory, getSchedule } from "./services/scheduleApi";
import { addMonths, fromDateKey, toDateKey } from "./utils/calendarUtils";

export default function App() {
  const [displayDate, setDisplayDate] = useState(
    () => new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  );
  const [schedule, setSchedule] = useState({});
  const [selectedDate, setSelectedDate] = useState(null);
  const [modalOrigin, setModalOrigin] = useState(null);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [changeHistory, setChangeHistory] = useState([]);
  const [activeDrawer, setActiveDrawer] = useState(null);
  const [scheduleLoading, setScheduleLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [scheduleError, setScheduleError] = useState(null);
  const [historyError, setHistoryError] = useState(null);
  const initialMonthSetRef = useRef(false);

  const refreshSchedule = useCallback(async ({ signal } = {}) => {
    setScheduleError(null);
    const payload = await getSchedule({ signal });
    setSchedule(payload.schedule);
    if (!initialMonthSetRef.current) {
      const dates = Object.keys(payload.schedule).sort();
      if (dates.length > 0) {
        const currentKey = toDateKey(new Date());
        const firstKey = dates[0];
        const lastKey = dates[dates.length - 1];
        if (currentKey < firstKey || currentKey > lastKey) {
          const firstDate = fromDateKey(firstKey);
          setDisplayDate(
            new Date(firstDate.getFullYear(), firstDate.getMonth(), 1),
          );
        }
      }
      initialMonthSetRef.current = true;
    }
  }, []);

  const refreshHistory = useCallback(async ({ signal } = {}) => {
    setHistoryError(null);
    const payload = await getChangeHistory({ signal });
    setChangeHistory(payload.groups);
  }, []);

  const refreshApprovedData = useCallback(async () => {
    const results = await Promise.allSettled([
      refreshSchedule(),
      refreshHistory(),
    ]);
    if (results[0].status === "rejected") {
      setScheduleError(results[0].reason);
    }
    if (results[1].status === "rejected") {
      setHistoryError(results[1].reason);
    }
  }, [refreshHistory, refreshSchedule]);

  const healing = useHealingRun({ onApproved: refreshApprovedData });

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([
      refreshSchedule({ signal: controller.signal }).finally(() =>
        setScheduleLoading(false),
      ),
      refreshHistory({ signal: controller.signal }).finally(() =>
        setHistoryLoading(false),
      ),
    ]).then(([scheduleResult, historyResult]) => {
      if (
        scheduleResult.status === "rejected" &&
        scheduleResult.reason.name !== "AbortError"
      ) {
        setScheduleError(scheduleResult.reason);
      }
      if (
        historyResult.status === "rejected" &&
        historyResult.reason.name !== "AbortError"
      ) {
        setHistoryError(historyResult.reason);
      }
    });
    return () => controller.abort();
  }, [refreshHistory, refreshSchedule]);

  const selectedDateKey = selectedDate ? toDateKey(selectedDate) : null;
  const selectedEvents = selectedDateKey
    ? schedule[selectedDateKey] ?? []
    : [];
  const hasOverlay = Boolean(selectedDate || activeDrawer);
  const runBusy =
    healing.isSubmitting ||
    healing.isResolving ||
    healing.run?.status === "processing";

  useEffect(() => {
    if (!hasOverlay) return undefined;

    document.body.classList.add("is-locked");
    function handleEscape(event) {
      if (event.key === "Escape" && !selectedDate) {
        setActiveDrawer(null);
      }
    }
    window.addEventListener("keydown", handleEscape);

    return () => {
      document.body.classList.remove("is-locked");
      window.removeEventListener("keydown", handleEscape);
    };
  }, [hasOverlay, selectedDate]);

  async function cancelEvents(eventIds) {
    return healing.start({
      cancellation_type: "events",
      event_ids: eventIds,
    });
  }

  async function cancelDay() {
    if (!selectedDateKey) return false;
    return healing.start({
      cancellation_type: "day",
      date: selectedDateKey,
    });
  }

  function openDayDetails(date, origin) {
    healing.reset();
    setModalOrigin(origin);
    setSelectedDate(date);
  }

  function closeDayDetails() {
    if (runBusy) return;
    setSelectedDate(null);
    setModalOrigin(null);
    healing.reset();
  }

  const inputPanel = (
    <FileUploadPanel
      files={uploadedFiles}
      onAddFile={(file) => setUploadedFiles((current) => [...current, file])}
      onRemoveFile={(fileId) =>
        setUploadedFiles((current) =>
          current.filter((file) => file.id !== fileId),
        )
      }
    />
  );

  const changesPanel = (
    <ChangeSummaryPanel
      changeHistory={changeHistory}
      isLoading={historyLoading}
      error={historyError}
    />
  );

  return (
    <div className="app-shell">
      {scheduleLoading || scheduleError ? (
        <div
          className={`app-status ${scheduleError ? "app-status--error" : ""}`}
          role={scheduleError ? "alert" : "status"}
        >
          {scheduleError
            ? scheduleError.message
            : "Loading the university schedule…"}
        </div>
      ) : null}

      <main className="app-layout">
        <div className="desktop-panel">{inputPanel}</div>
        <Calendar
          displayDate={displayDate}
          schedule={schedule}
          onSelectDate={openDayDetails}
          onPreviousMonth={() =>
            setDisplayDate((current) => addMonths(current, -1))
          }
          onNextMonth={() =>
            setDisplayDate((current) => addMonths(current, 1))
          }
          onOpenInputs={() => setActiveDrawer("inputs")}
          onOpenChanges={() => setActiveDrawer("changes")}
        />
        <div className="desktop-panel">{changesPanel}</div>
      </main>

      <MobileDrawer
        title="Schedule Inputs"
        isOpen={activeDrawer === "inputs"}
        onClose={() => setActiveDrawer(null)}
      >
        {inputPanel}
      </MobileDrawer>
      <MobileDrawer
        title="AI Schedule Changes"
        isOpen={activeDrawer === "changes"}
        onClose={() => setActiveDrawer(null)}
      >
        {changesPanel}
      </MobileDrawer>

      <DayDetailsModal
        date={selectedDate}
        events={selectedEvents}
        origin={modalOrigin}
        healingRun={healing.run}
        healingError={healing.error}
        isSubmitting={healing.isSubmitting}
        isResolving={healing.isResolving}
        onClose={closeDayDetails}
        onCancelEvents={cancelEvents}
        onCancelDay={cancelDay}
        onApprove={healing.approve}
        onReject={healing.reject}
      />
    </div>
  );
}
