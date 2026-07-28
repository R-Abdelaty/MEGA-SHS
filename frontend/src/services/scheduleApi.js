const API_BASE_URL = (
  import.meta.env.VITE_SCHEDULER_API_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");
const API_KEY = import.meta.env.VITE_SCHEDULER_UI_API_KEY || "";
const WORKSPACE_ID =
  import.meta.env.VITE_MEGA_SHS_WORKSPACE_ID || "mega-shs-local";

export class ScheduleApiError extends Error {
  constructor(message, { code = "API_ERROR", status = 0, payload = null } = {}) {
    super(message);
    this.name = "ScheduleApiError";
    this.code = code;
    this.status = status;
    this.payload = payload;
  }
}

async function request(path, { method = "GET", body, signal } = {}) {
  const headers = {
    Accept: "application/json",
    "X-Workspace-ID": WORKSPACE_ID,
  };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ScheduleApiError("The MEGA-SHS API is unavailable.", {
      code: "NETWORK_ERROR",
    });
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const apiError = payload?.error;
    throw new ScheduleApiError(
      apiError?.message || `The API request failed (${response.status}).`,
      {
        code: apiError?.code || "API_ERROR",
        status: response.status,
        payload,
      },
    );
  }
  return payload;
}

function capitalize(value) {
  if (!value) return "";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function toCalendarEvent(event) {
  return {
    id: event.id,
    name: event.name,
    room: event.room,
    type: capitalize(event.type),
    studentGroup: event.student_group,
    date: event.date,
    startTime: event.start_time,
    endTime: event.end_time,
    status: capitalize(event.status),
  };
}

export async function getSchedule({ signal } = {}) {
  const payload = await request("/api/schedule", { signal });
  const schedule = {};
  for (const rawEvent of payload.events) {
    const event = toCalendarEvent(rawEvent);
    if (!schedule[event.date]) schedule[event.date] = [];
    schedule[event.date].push(event);
  }
  return { ...payload, schedule };
}

export function createHealingRun(cancellation, { signal } = {}) {
  return request("/api/healing-runs", {
    method: "POST",
    body: cancellation,
    signal,
  });
}

export function getHealingRun(runId, { signal } = {}) {
  return request(`/api/healing-runs/${encodeURIComponent(runId)}`, { signal });
}

export function approveHealingRun(runId, { signal } = {}) {
  return request(`/api/healing-runs/${encodeURIComponent(runId)}/approve`, {
    method: "POST",
    signal,
  });
}

export function rejectHealingRun(runId, { signal } = {}) {
  return request(`/api/healing-runs/${encodeURIComponent(runId)}/reject`, {
    method: "POST",
    signal,
  });
}

export function getChangeHistory({ signal } = {}) {
  return request("/api/change-history", { signal });
}

export function exportSchedule({ signal } = {}) {
  return request("/api/schedule/export", { method: "POST", signal });
}

