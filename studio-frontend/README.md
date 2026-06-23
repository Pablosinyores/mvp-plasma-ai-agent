# Plasma Agent Studio — Frontend

A standalone **React + Vite + TypeScript** control-plane UI for the MVP Plasma AI Agent backend.
It runs on its **own dev/prod server** and talks to the FastAPI backend over **REST + WebSocket** —
no code is shared with the Python backend, only the JSON contract.

```
studio-frontend/                 ← this folder (the FE module)
├── index.html
├── package.json
├── vite.config.ts
├── .env.example                 ← VITE_API_BASE / VITE_WS_URL
└── src/
    ├── main.tsx                 ← app entry (mounts StudioProvider + App)
    ├── App.tsx                  ← TopBar + Stats + dynamic sections
    ├── config.ts                ← backend URL wiring (env-driven)
    ├── types.ts                 ← JSON contract mirror of the backend
    ├── store.tsx                ← shared UI services: toasts, activity log, modal
    ├── api/
    │   ├── client.ts            ← REST calls (one per `studio` CLI op)
    │   └── useLiveState.ts      ← WebSocket live state + auto-reconnect
    ├── hooks/useCountUp.ts      ← eased stat counters
    ├── lib/format.ts            ← address/number formatting
    ├── sections/registry.tsx    ← ⭐ dynamic section list — add future sections here
    └── components/              ← TopBar, Stats, AgentsSection, JobsSection,
        └── modals/                FeedSecuritySection, SecurityDrill, ActivityLog, …
```

## What it does

Every action maps 1:1 to a backend endpoint, which reuses the **same helpers the demo scripts use**:

| UI action            | Endpoint                       | Equivalent script step          |
|----------------------|--------------------------------|---------------------------------|
| create agent         | `POST /api/agents`             | `studio create`                 |
| resolve identity     | `GET  /api/agents/{n}/resolve` | `studio resolve`                |
| fund job             | `POST /api/jobs`               | `studio fund-job`               |
| x402 spend           | `POST /api/spend`              | `demo3` SPEND block             |
| auto-refuel          | `POST /api/refuel`             | `demo3` AUTO-REFUEL block       |
| injection drill      | `POST /api/injection-test`     | Phase 5 (guards block a drain)  |
| live state (no poll) | `WS /ws`                       | dashboard broadcaster           |

## Run

The backend must be up first (`make up && make model`, then `python3 backend/cli/studio.py dashboard`
serving on `:8080`). Then, from this folder:

```bash
cp .env.example .env          # optional — defaults already point at http://localhost:8080
pnpm install
pnpm dev                      # → http://localhost:5173
```

For a production bundle:

```bash
pnpm build                    # → dist/   (static; serve behind any web server)
pnpm preview                  # preview the built bundle
```

## Pointing at a different backend

Set in `.env`:

```
VITE_API_BASE=http://localhost:8080
# VITE_WS_URL=ws://localhost:8080/ws   # derived from API_BASE if omitted
```

## Adding a new section

The page is assembled from `src/sections/registry.tsx`. To add a section, write a component that
takes `{ state }: SectionProps` and append one entry:

```tsx
export const SECTIONS: SectionDef[] = [
  { id: "agents", Component: AgentsSection },
  { id: "jobs", Component: JobsSection },
  { id: "feed-security", Component: FeedSecuritySection },
  { id: "activity-log", Component: ActivityLogSection },
  // { id: "disputes", Component: DisputesSection },   ← future
];
```

No other file needs to change — `App.tsx` maps over the registry.
