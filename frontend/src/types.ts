export type SessionOutcome = "running" | "human_won" | "human_lost";
export type PlayerType = "human" | "bot";
export type ClientActionType =
  | "fold"
  | "check"
  | "call"
  | "bet"
  | "raise"
  | "all_in";

export type EventType =
  | "HAND_START"
  | "POST_BLIND"
  | "DEAL_CARD"
  | "ACTION"
  | "STREET_END_COLLECT"
  | "BOARD_REVEAL"
  | "SHOWDOWN_REVEAL"
  | "POT_AWARD"
  | "STACK_UPDATE"
  | "HAND_END"
  | "SESSION_END";

export interface SeatState {
  seat_id: number;
  player_type: PlayerType;
  display_name: string;
  stack: number;
  has_folded: boolean;
  is_all_in: boolean;
  is_busted: boolean;
  role_badge: "SB" | "BB" | null;
  is_dealer_button: boolean;
  cards: (string | null)[];
}

export interface PotView {
  pot_id: number;
  amount: number;
  eligible_seats: number[];
  label: string;
}

export interface ActionLogEntry {
  event_seq: number;
  seat_id: number;
  action: ClientActionType;
  amount_to: number | null;
  street: "preflop" | "flop" | "turn" | "river" | "showdown" | "hand_ended";
}

export interface AllowedActions {
  can_fold: boolean;
  can_check: boolean;
  can_call: boolean;
  can_bet: boolean;
  can_raise: boolean;
  can_all_in: boolean;
  call_amount: number;
  min_bet_to: number | null;
  min_raise_to: number | null;
  max_raise_to: number | null;
  pot_size: number;
  effective_stack: number;
}

export interface ShowdownRow {
  seat_id: number;
  player_name: string;
  hole_cards: string[];
  best_hand_name: string;
  hand_rank_value: number;
  amount_won: number;
}

export interface ShowdownPayload {
  winners: ShowdownRow[];
  losers: ShowdownRow[];
}

export interface ViewState {
  table_id: string;
  hand_id: number | null;
  session_outcome: SessionOutcome;
  seats: SeatState[];
  board_cards: string[];
  pots: PotView[];
  chips_in_front: Record<number, number>;
  action_on_seat: number | null;
  turn_index: number | null;
  action_log: ActionLogEntry[];
  server_action_seq: number;
  allowed_actions: AllowedActions;
  showdown_payload: ShowdownPayload | null;
  state_hash: string;
  invariant_hash: string;
  speed_label: string;
}

export interface EventEnvelope {
  table_id: string;
  hand_id: number;
  event_seq: number;
  ts: string;
  event_type: EventType;
  payload: Record<string, unknown>;
}

export interface SubmitActionResponse {
  accepted: boolean;
  error?: { code: string; message: string } | null;
  view_state: ViewState;
  event_queue_delta: EventEnvelope[];
  server_action_seq: number;
}

export interface CreateTableResponse {
  table_id: string;
  start: {
    view_state: ViewState;
    event_queue: EventEnvelope[];
  };
}
