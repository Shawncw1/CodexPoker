# CodexPoker

Server-authoritative No-Limit Texas Hold'em (NLHE) MVP:
- `1` human vs `5` bots
- cash-game loop (50/100 blinds, 10,000 start stack, no rake)
- strict backend legality checks + deterministic hand replay/testing
- React web table UI with sequenced event animation orchestration

## Architecture Overview

### Backend (authoritative)
- Python 3.11+, FastAPI, WebSockets, PokerKit
- Engine wrapper (`PokerEngineService`) is the single source of truth
- Client submits intent only; server validates action legality and sizing
- Deterministic seeds for:
  - deal order
  - bot decision randomness
  - bot delay randomness
- Hand history export + replay invariant checks

### Frontend
- React + TypeScript (Vite)
- WebSocket-driven event feed + HTTP view refresh
- Renders server `ViewState` only (no optimistic legality)
- `AnimationOrchestrator` processes server events sequentially

## Repo File Tree

```text
.
├── AGENTS.md
├── backend
│   ├── pyproject.toml
│   ├── src/codexpoker_backend
│   │   ├── main.py
│   │   ├── api/
│   │   ├── bots/
│   │   ├── engine/
│   │   ├── repo/
│   │   ├── tools/replay_cli.py
│   │   └── utils/
│   └── tests/
├── frontend
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── App.css
│       ├── api.ts
│       ├── animations.ts
│       ├── orchestrator.ts
│       ├── types.ts
│       └── usePokerGame.ts
└── README.md
```

## API Spec (HTTP + WS)

### HTTP

- `POST /api/tables`
  - Creates table + starts first hand.
  - Response: `{ table_id, start: { view_state, event_queue } }`

- `GET /api/tables/{table_id}/view`
  - Returns current viewer-safe `ViewState`.

- `GET /api/tables/{table_id}/allowed-actions`
  - Returns `AllowedActions` for human seat.

- `POST /api/tables/{table_id}/actions`
  - Request:
    ```json
    {
      "action": "call",
      "amount_to": null,
      "action_seq": 7,
      "idempotency_key": "abc-123"
    }
    ```
  - Response:
    ```json
    {
      "accepted": true,
      "error": null,
      "view_state": {},
      "event_queue_delta": [],
      "server_action_seq": 8
    }
    ```

- `POST /api/tables/{table_id}/restart`
  - Resets session stacks and starts a new session.

- `GET /api/tables/{table_id}/hands/{hand_id}/history?mode=viewer|debug`
  - Canonical hand history JSON export.

- `POST /api/replay`
  - Request: `{ "hand_history": { ... } }`
  - Response: replay terminal state + invariant checks.

### WebSocket

- `WS /api/ws/tables/{table_id}`
- Message envelopes:
  - `{"type":"VIEW_STATE","payload":{...}}`
  - `{"type":"EVENT","payload":{...}}`

Event payload fields:
- `table_id`, `hand_id`, `event_seq`, `ts`, `event_type`, `payload`

Supported `event_type`:
- `HAND_START`
- `POST_BLIND`
- `DEAL_CARD`
- `ACTION`
- `STREET_END_COLLECT`
- `BOARD_REVEAL`
- `SHOWDOWN_REVEAL`
- `POT_AWARD`
- `STACK_UPDATE`
- `HAND_END`
- `SESSION_END`

## Data Models (Example JSON)

### ViewState example

```json
{
  "table_id": "tbl_abc123",
  "hand_id": 4,
  "session_outcome": "running",
  "seats": [
    {
      "seat_id": 0,
      "player_type": "human",
      "display_name": "You",
      "stack": 9800,
      "has_folded": false,
      "is_all_in": false,
      "is_busted": false,
      "role_badge": "SB",
      "is_dealer_button": false,
      "cards": ["Ah", "Kd"]
    }
  ],
  "board_cards": ["2c", "7d", "Js"],
  "pots": [
    {
      "pot_id": 0,
      "amount": 900,
      "eligible_seats": [0, 1, 2, 3],
      "label": "POT"
    }
  ],
  "chips_in_front": { "0": 200, "1": 200, "2": 0, "3": 0, "4": 0, "5": 0 },
  "action_on_seat": 0,
  "turn_index": 0,
  "action_log": [],
  "server_action_seq": 9,
  "allowed_actions": {
    "can_fold": true,
    "can_check": false,
    "can_call": true,
    "can_bet": false,
    "can_raise": true,
    "can_all_in": true,
    "call_amount": 100,
    "min_bet_to": null,
    "min_raise_to": 400,
    "max_raise_to": 9800,
    "pot_size": 900,
    "effective_stack": 9800
  },
  "showdown_payload": null,
  "state_hash": "...",
  "invariant_hash": "...",
  "speed_label": "1x"
}
```

## Animation Config + Orchestrator

- Central timing config: `frontend/src/animations.ts` (`Anim`)
- Sequential event queue runner: `frontend/src/orchestrator.ts`
- Frontend does not infer legality from animation state.
- Events animate in strict server `event_seq` order.

## Testing Plan + Invariants

Implemented in `backend/tests`:
- Golden deterministic replay checks
- Multi-way all-in scenario coverage
- Min-raise sizing rejection
- Heads-up blind/action order checks
- Idempotency behavior checks
- Masking/showdown reveal checks
- Fuzz/property-style random legal-action runs

Core invariants checked:
- no negative stacks
- chip conservation
- legal action enforcement
- hand termination
- replay terminal-state match
- monotonic event sequencing

## Step-by-Step Build Plan (Implemented)

1. Scaffold backend + frontend monorepo.
2. Implement server-authoritative engine wrapper around PokerKit.
3. Add deterministic events, hand-history export, replay checks.
4. Add backend tests (golden + fuzz + masking + replay).
5. Build React table UI, action controls, slider workflow.
6. Add websocket event orchestrator and run/test instructions.

## Run Instructions

### 1) Backend

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e 'backend[dev]'
uvicorn codexpoker_backend.main:app --reload --host 127.0.0.1 --port 8000
```

### 2) Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` and websocket traffic to backend on `127.0.0.1:8000`.

### 3) Tests

```bash
source .venv/bin/activate
pytest backend/tests -q
```

### 4) Replay CLI

```bash
source .venv/bin/activate
python -m codexpoker_backend.tools.replay_cli /path/to/hand_history.json
```
