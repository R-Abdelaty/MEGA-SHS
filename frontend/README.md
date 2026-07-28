# MEGA-SHS frontend

Responsive Vite + React prototype for the MEGA-SHS university scheduling
interface. It uses JavaScript, plain CSS, mock data, and local React state.

## Run locally

```bash
npm install
npm run dev
```

Open the local URL printed by Vite.

## Production build

```bash
npm run lint
npm run build
```

## Future FastAPI integration

All backend-facing placeholders live in
`src/services/scheduleApi.js`. Configure the future FastAPI base URL there and
replace the mock implementations of:

- `getSchedule(year, month)`
- `uploadScheduleFile(file, label)`
- `cancelScheduleEvents(eventIds)`
- `cancelScheduleDay(date)`
- `getAgentChangeHistory()`

UI components should continue to receive data and mutation handlers through
props instead of calling FastAPI directly.
