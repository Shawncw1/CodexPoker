import { useMemo, useState } from "react";

import "./App.css";
import { usePokerGame } from "./usePokerGame";
import type { AllowedActions, ClientActionType, ShowdownRow } from "./types";

type ArmMode = "bet" | "raise" | null;

const SEAT_LAYOUT = [
  { x: 50, y: 86 },
  { x: 20, y: 68 },
  { x: 20, y: 32 },
  { x: 50, y: 14 },
  { x: 80, y: 32 },
  { x: 80, y: 68 },
];

function App() {
  const { view, loading, error, badges, potPulse, speed, submit, restart } = usePokerGame();
  const [armMode, setArmMode] = useState<ArmMode>(null);
  const [sliderTo, setSliderTo] = useState(0);

  const allowed = view?.allowed_actions;
  const sliderBounds = useMemo(() => getSliderBounds(allowed, armMode), [allowed, armMode]);
  const sliderValue = sliderBounds ? clamp(sliderTo || sliderBounds.min, sliderBounds.min, sliderBounds.max) : 0;

  if (loading) {
    return <div className="screen-center">Starting table...</div>;
  }

  if (!view) {
    return <div className="screen-center">No table state available.</div>;
  }

  const canAct = view.session_outcome === "running";
  const callLabel = allowed?.call_amount ? `CALL ${allowed.call_amount}` : "CALL";
  const sessionEnded = view.session_outcome !== "running";
  const endText = view.session_outcome === "human_won" ? "You won!" : "You lost";

  return (
    <div className="app">
      <header className="top-bar">
        <div>
          <h1>CodexPoker</h1>
          <p>No-Limit Texas Hold&apos;em · 50/100 · 6-max</p>
        </div>
        <button className="speed-chip" type="button" aria-label="Speed Toggle">
          {speed}x
        </button>
      </header>

      <section className="table-shell">
        <div className="table-surface">
          <div className="table-felt">
            <div className="board-lane">
              <div className={`pot-core ${potPulse ? "pulse" : ""}`}>
                <div className="pot-chip-stack" />
                <span className="pot-label">POT</span>
                <strong>{view.pots.reduce((sum, pot) => sum + pot.amount, 0)}</strong>
              </div>

              <div className="side-pots">
                {view.pots
                  .filter((pot) => pot.pot_id > 0)
                  .map((pot) => (
                    <div key={pot.pot_id} className="side-pot">
                      <span>{pot.label}</span>
                      <strong>{pot.amount}</strong>
                    </div>
                  ))}
              </div>

              <div className="board-cards">
                {Array.from({ length: 5 }).map((_, index) => (
                  <Card key={`board-${index}`} code={view.board_cards[index] ?? null} />
                ))}
              </div>
            </div>

            {view.seats.map((seat) => {
              const position = SEAT_LAYOUT[seat.seat_id] ?? { x: 50, y: 50 };
              const isActing = view.action_on_seat === seat.seat_id && canAct;
              return (
                <div
                  key={seat.seat_id}
                  className={`seat ${seat.is_busted ? "busted" : ""}`}
                  style={{ left: `${position.x}%`, top: `${position.y}%` }}
                >
                  <div className={`avatar ${isActing ? "acting" : ""}`}>
                    {seat.is_dealer_button && <span className="dealer-dot">D</span>}
                    {seat.role_badge && <span className="role-badge">{seat.role_badge}</span>}
                    <span>{initials(seat.display_name)}</span>
                  </div>
                  <div className="seat-meta">
                    <span>{seat.display_name}</span>
                    <strong>{seat.stack}</strong>
                  </div>
                  <div className="hole-cards">
                    <Card code={seat.cards[0] ?? null} />
                    <Card code={seat.cards[1] ?? null} />
                  </div>
                  <div className="chips-in-front">
                    <div className="mini-stack" />
                    <span>{view.chips_in_front[seat.seat_id] ?? 0}</span>
                  </div>
                  {badges[seat.seat_id] && <div className="action-badge">{badges[seat.seat_id]}</div>}
                </div>
              );
            })}
          </div>
        </div>

        <aside className="right-rail">
          <div className="bet-preview">
            <div className="mini-stack grow" style={{ height: `${Math.max(16, (sliderTo / Math.max(sliderBounds?.max ?? 1, 1)) * 82)}px` }} />
            <strong>{sliderValue || 0}</strong>
          </div>

          <div className={`slider-panel ${armMode ? "armed" : ""}`}>
            <h3>{armMode ? `${armMode.toUpperCase()} TO` : "BET SLIDER"}</h3>
            <input
              type="range"
              className="bet-slider"
              disabled={!armMode || !sliderBounds}
              min={sliderBounds?.min ?? 0}
              max={sliderBounds?.max ?? 0}
              step={1}
              value={sliderValue}
              onChange={(event) => setSliderTo(Number(event.target.value))}
            />
            <div className="quick-actions">
              <button type="button" onClick={() => setSliderByPot(1 / 3, allowed, sliderBounds, setSliderTo)}>
                1/3 POT
              </button>
              <button type="button" onClick={() => setSliderByPot(1 / 2, allowed, sliderBounds, setSliderTo)}>
                1/2 POT
              </button>
              <button type="button" onClick={() => setSliderByPot(1, allowed, sliderBounds, setSliderTo)}>
                POT
              </button>
              <button type="button" onClick={() => sliderBounds && setSliderTo(sliderBounds.max)}>
                ALL-IN
              </button>
            </div>
            {armMode && (
              <div className="arm-controls">
                <button
                  type="button"
                  className="confirm"
                  onClick={() => {
                    void submit(armMode as ClientActionType, sliderValue);
                    setArmMode(null);
                  }}
                >
                  CONFIRM
                </button>
                <button type="button" onClick={() => setArmMode(null)}>
                  CANCEL
                </button>
              </div>
            )}
          </div>
        </aside>
      </section>

      <footer className="action-bar">
        <ActionButton
          label="FOLD"
          disabled={!canAct || !allowed?.can_fold}
          onClick={() => void submit("fold")}
        />
        <ActionButton
          label="CHECK"
          disabled={!canAct || !allowed?.can_check}
          onClick={() => void submit("check")}
        />
        <ActionButton
          label={callLabel}
          disabled={!canAct || !allowed?.can_call}
          onClick={() => void submit("call")}
        />
        <ActionButton
          label="BET"
          disabled={!canAct || !allowed?.can_bet}
          onClick={() => armSlider("bet", allowed, setArmMode, setSliderTo)}
        />
        <ActionButton
          label="RAISE"
          disabled={!canAct || !allowed?.can_raise}
          onClick={() => armSlider("raise", allowed, setArmMode, setSliderTo)}
        />
        <ActionButton
          label="ALL-IN"
          disabled={!canAct || !allowed?.can_all_in}
          onClick={() => void submit("all_in", allowed?.max_raise_to ?? null)}
        />
      </footer>

      <aside className="log-panel">
        <h3>Action Log</h3>
        <ul>
          {view.action_log.slice().reverse().map((entry) => (
            <li key={`${entry.event_seq}-${entry.seat_id}`}>
              <span>{view.seats[entry.seat_id]?.display_name ?? `Seat ${entry.seat_id}`}</span>
              <strong>
                {entry.action.toUpperCase()}
                {entry.amount_to ? ` ${entry.amount_to}` : ""}
              </strong>
            </li>
          ))}
        </ul>
      </aside>

      {view.showdown_payload && (
        <section className="showdown-panel">
          <h2>Showdown</h2>
          <div className="showdown-columns">
            <div>
              <h3>Winners</h3>
              {view.showdown_payload.winners.map((row) => (
                <ShowdownItem key={`w-${row.seat_id}-${row.amount_won}`} row={row} />
              ))}
            </div>
            <div>
              <h3>Losers</h3>
              {view.showdown_payload.losers.map((row) => (
                <ShowdownItem key={`l-${row.seat_id}-${row.hand_rank_value}`} row={row} />
              ))}
            </div>
          </div>
        </section>
      )}

      {sessionEnded && (
        <div className="session-overlay">
          <div className="session-card">
            <h2>{endText}</h2>
            <button type="button" onClick={() => void restart()}>
              Restart
            </button>
          </div>
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}
    </div>
  );
}

function ActionButton(props: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" className="action-button" disabled={props.disabled} onClick={props.onClick}>
      {props.label}
    </button>
  );
}

function Card({ code }: { code: string | null }) {
  if (!code) {
    return <div className="card back" />;
  }
  const rank = code.slice(0, -1);
  const suit = code.slice(-1);
  const red = suit === "h" || suit === "d";
  return (
    <div className={`card face ${red ? "red" : ""}`}>
      <span>{rank}</span>
      <span>{suitSymbol(suit)}</span>
    </div>
  );
}

function ShowdownItem({ row }: { row: ShowdownRow }) {
  return (
    <div className="showdown-row">
      <span>{row.player_name}</span>
      <span>{row.hole_cards.join(" ")}</span>
      <span>{row.best_hand_name}</span>
      <strong>{row.amount_won}</strong>
    </div>
  );
}

function suitSymbol(suit: string): string {
  switch (suit) {
    case "h":
      return "♥";
    case "d":
      return "♦";
    case "c":
      return "♣";
    case "s":
      return "♠";
    default:
      return "?";
  }
}

function initials(name: string): string {
  return name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function getSliderBounds(allowed: AllowedActions | undefined, armMode: ArmMode) {
  if (!allowed || !armMode) {
    return null;
  }
  if (armMode === "bet" && allowed.min_bet_to != null && allowed.max_raise_to != null) {
    return { min: allowed.min_bet_to, max: allowed.max_raise_to };
  }
  if (armMode === "raise" && allowed.min_raise_to != null && allowed.max_raise_to != null) {
    return { min: allowed.min_raise_to, max: allowed.max_raise_to };
  }
  return null;
}

function setSliderByPot(
  ratio: number,
  allowed: AllowedActions | undefined,
  sliderBounds: { min: number; max: number } | null,
  setter: (amount: number) => void,
) {
  if (!allowed || !sliderBounds) {
    return;
  }
  const raw = Math.round(allowed.pot_size * ratio);
  setter(clamp(raw, sliderBounds.min, sliderBounds.max));
}

function armSlider(
  mode: Exclude<ArmMode, null>,
  allowed: AllowedActions | undefined,
  setArmMode: (mode: ArmMode) => void,
  setSliderTo: (value: number) => void,
) {
  const bounds = getSliderBounds(allowed, mode);
  setArmMode(mode);
  if (bounds) {
    setSliderTo(bounds.min);
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export default App;
