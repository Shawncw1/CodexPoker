import type {
  ClientActionType,
  CreateTableResponse,
  EventEnvelope,
  SubmitActionResponse,
  ViewState,
} from "./types";

const API_BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function createTable(): Promise<CreateTableResponse> {
  return request<CreateTableResponse>("/tables", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function submitAction(args: {
  tableId: string;
  action: ClientActionType;
  actionSeq: number;
  idempotencyKey: string;
  amountTo?: number | null;
}): Promise<SubmitActionResponse> {
  return request<SubmitActionResponse>(`/tables/${args.tableId}/actions`, {
    method: "POST",
    body: JSON.stringify({
      action: args.action,
      amount_to: args.amountTo ?? null,
      action_seq: args.actionSeq,
      idempotency_key: args.idempotencyKey,
    }),
  });
}

export async function restartSession(tableId: string): Promise<{
  view_state: ViewState;
  event_queue?: EventEnvelope[];
}> {
  return request(`/tables/${tableId}/restart`, { method: "POST", body: JSON.stringify({}) });
}

export async function getView(tableId: string): Promise<ViewState> {
  return request<ViewState>(`/tables/${tableId}/view`);
}
