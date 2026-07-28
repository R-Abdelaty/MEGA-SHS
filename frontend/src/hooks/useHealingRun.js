import { useCallback, useEffect, useRef, useState } from "react";
import {
  approveHealingRun,
  createHealingRun,
  getHealingRun,
  rejectHealingRun,
} from "../services/scheduleApi";

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 240000;

export default function useHealingRun({ onApproved } = {}) {
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResolving, setIsResolving] = useState(false);
  const submitControllerRef = useRef(null);
  const submissionLockRef = useRef(false);
  const resolutionLockRef = useRef(false);
  const onApprovedRef = useRef(onApproved);

  useEffect(() => {
    onApprovedRef.current = onApproved;
  }, [onApproved]);

  const start = useCallback(
    async (cancellation) => {
      if (
        submissionLockRef.current ||
        isSubmitting ||
        run?.status === "processing"
      ) {
        return false;
      }
      submissionLockRef.current = true;
      submitControllerRef.current?.abort();
      const controller = new AbortController();
      submitControllerRef.current = controller;
      setIsSubmitting(true);
      setError(null);
      try {
        const created = await createHealingRun(cancellation, {
          signal: controller.signal,
        });
        setRun(created);
        return true;
      } catch (requestError) {
        if (requestError.name !== "AbortError") setError(requestError);
        return false;
      } finally {
        submissionLockRef.current = false;
        setIsSubmitting(false);
      }
    },
    [isSubmitting, run?.status],
  );

  useEffect(() => {
    if (run?.status !== "processing" || !run.run_id) return undefined;
    const controller = new AbortController();
    const startedAt = Date.now();
    let timerId;

    async function poll() {
      try {
        const nextRun = await getHealingRun(run.run_id, {
          signal: controller.signal,
        });
        setError(null);
        setRun(nextRun);
        if (nextRun.status === "processing") {
          if (Date.now() - startedAt >= POLL_TIMEOUT_MS) {
            setError(
              new Error(
                "The healing run is taking longer than expected. Try again.",
              ),
            );
            return;
          }
          timerId = window.setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (requestError) {
        if (requestError.name === "AbortError") return;
        setError(requestError);
        if (Date.now() - startedAt < POLL_TIMEOUT_MS) {
          timerId = window.setTimeout(poll, POLL_INTERVAL_MS);
        }
      }
    }

    poll();
    return () => {
      controller.abort();
      if (timerId) window.clearTimeout(timerId);
    };
  }, [run?.run_id, run?.status]);

  useEffect(
    () => () => {
      submitControllerRef.current?.abort();
    },
    [],
  );

  const approve = useCallback(async () => {
    if (
      resolutionLockRef.current ||
      isResolving ||
      run?.status !== "approval_required"
    ) {
      return false;
    }
    resolutionLockRef.current = true;
    setIsResolving(true);
    setError(null);
    try {
      const result = await approveHealingRun(run.run_id);
      setRun((current) => ({ ...current, ...result }));
      await onApprovedRef.current?.();
      return true;
    } catch (requestError) {
      if (requestError.payload?.status === "stale") {
        try {
          setRun(await getHealingRun(run.run_id));
        } catch {
          setRun((current) => ({ ...current, status: "stale" }));
        }
      }
      setError(requestError);
      return false;
    } finally {
      resolutionLockRef.current = false;
      setIsResolving(false);
    }
  }, [isResolving, run]);

  const reject = useCallback(async () => {
    if (
      resolutionLockRef.current ||
      isResolving ||
      run?.status !== "approval_required"
    ) {
      return false;
    }
    resolutionLockRef.current = true;
    setIsResolving(true);
    setError(null);
    try {
      const result = await rejectHealingRun(run.run_id);
      setRun((current) => ({ ...current, ...result }));
      return true;
    } catch (requestError) {
      setError(requestError);
      return false;
    } finally {
      resolutionLockRef.current = false;
      setIsResolving(false);
    }
  }, [isResolving, run]);

  const reset = useCallback(() => {
    submitControllerRef.current?.abort();
    submissionLockRef.current = false;
    resolutionLockRef.current = false;
    setRun(null);
    setError(null);
    setIsSubmitting(false);
    setIsResolving(false);
  }, []);

  return {
    run,
    error,
    isSubmitting,
    isResolving,
    start,
    approve,
    reject,
    reset,
  };
}
