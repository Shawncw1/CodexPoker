import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { createTable, getView, restartSession, submitAction } from "./api";
import { AnimationOrchestrator } from "./orchestrator";
import type { ClientActionType, EventEnvelope, ViewState } from "./types";

interface BadgeState {
  [seatId: number]: string | undefined;
}

export function usePokerGame() {
  const [tableId, setTableId] = useState<string | null>(null);
  const [view, setView] = useState<ViewState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [badges, setBadges] = useState<BadgeState>({});
  const [potPulse, setPotPulse] = useState(0);
  const [speed, setSpeed] = useState<1>(1);

  const refreshTimer = useRef<number | null>(null);

  const orchestrator = useMemo(
    () =>
      new AnimationOrchestrator(
        (event) => {
          if (event.event_type === "ACTION") {
            const seat = Number(event.payload.seat);
            const action = String(event.payload.action_type ?? "").replace("_", " ").toUpperCase();
            setBadges((current) => ({ ...current, [seat]: action }));
          }
          if (event.event_type === "POT_AWARD") {
            setPotPulse((count) => count + 1);
          }
          if (event.event_type === "HAND_START") {
            setBadges({});
          }
        },
        (event) => {
          if (event.event_type === "ACTION") {
            const seat = Number(event.payload.seat);
            setBadges((current) => ({ ...current, [seat]: undefined }));
          }
        },
      ),
    [],
  );

  const scheduleRefresh = useCallback(
    (id: string) => {
      if (refreshTimer.current !== null) {
        return;
      }
      refreshTimer.current = window.setTimeout(async () => {
        refreshTimer.current = null;
        try {
          const latest = await getView(id);
          setView(latest);
        } catch (refreshError) {
          setError((refreshError as Error).message);
        }
      }, 60);
    },
    [],
  );

  const enqueue = useCallback(
    (events: EventEnvelope[]) => {
      if (events.length === 0) {
        return;
      }
      orchestrator.enqueue(events);
    },
    [orchestrator],
  );

  const boot = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const created = await createTable();
      setTableId(created.table_id);
      setView(created.start.view_state);
      enqueue(created.start.event_queue);
    } catch (bootError) {
      setError((bootError as Error).message);
    } finally {
      setLoading(false);
    }
  }, [enqueue]);

  useEffect(() => {
    void boot();
  }, [boot]);

  useEffect(() => {
    if (!tableId) {
      return;
    }
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}/api/ws/tables/${tableId}`);

    socket.onmessage = (rawEvent) => {
      try {
        const packet = JSON.parse(rawEvent.data) as { type: "VIEW_STATE" | "EVENT"; payload: unknown };
        if (packet.type === "VIEW_STATE") {
          setView(packet.payload as ViewState);
          return;
        }
        const event = packet.payload as EventEnvelope;
        enqueue([event]);
        scheduleRefresh(tableId);
      } catch (socketError) {
        console.error("socket parse error", socketError);
      }
    };

    socket.onerror = () => {
      setError("websocket connection failed");
    };

    return () => {
      socket.close();
    };
  }, [tableId, enqueue, scheduleRefresh]);

  const submit = useCallback(
    async (action: ClientActionType, amountTo?: number | null) => {
      if (!tableId || !view) {
        return;
      }
      setError(null);
      try {
        const response = await submitAction({
          tableId,
          action,
          amountTo,
          actionSeq: view.server_action_seq + 1,
          idempotencyKey: `${tableId}-${view.hand_id ?? 0}-${view.server_action_seq + 1}-${Date.now()}`,
        });
        setView(response.view_state);
        enqueue(response.event_queue_delta);
        if (!response.accepted && response.error) {
          setError(response.error.message);
        }
      } catch (submitError) {
        setError((submitError as Error).message);
      }
    },
    [tableId, view, enqueue],
  );

  const restart = useCallback(async () => {
    if (!tableId) {
      return;
    }
    setError(null);
    try {
      const response = await restartSession(tableId);
      setView(response.view_state);
      enqueue(response.event_queue ?? []);
    } catch (restartError) {
      setError((restartError as Error).message);
    }
  }, [tableId, enqueue]);

  return {
    tableId,
    view,
    loading,
    error,
    badges,
    potPulse,
    speed,
    setSpeed,
    submit,
    restart,
  };
}
