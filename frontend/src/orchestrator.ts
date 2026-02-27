import { durationForEvent } from "./animations";
import type { EventEnvelope } from "./types";

type EventHandler = (event: EventEnvelope) => void;

export class AnimationOrchestrator {
  private queue: EventEnvelope[] = [];
  private processing = false;
  private seen = new Set<string>();
  private readonly onStart: EventHandler;
  private readonly onFinish?: EventHandler;

  constructor(onStart: EventHandler, onFinish?: EventHandler) {
    this.onStart = onStart;
    this.onFinish = onFinish;
  }

  enqueue(events: EventEnvelope[]): void {
    for (const event of events) {
      const key = `${event.hand_id}:${event.event_seq}`;
      if (this.seen.has(key)) {
        continue;
      }
      this.seen.add(key);
      this.queue.push(event);
    }
    this.process().catch((error) => {
      console.error("orchestrator error", error);
      this.processing = false;
    });
  }

  private async process(): Promise<void> {
    if (this.processing) {
      return;
    }
    this.processing = true;
    while (this.queue.length > 0) {
      const next = this.queue.shift();
      if (!next) {
        continue;
      }
      this.onStart(next);
      await sleep(durationForEvent(next));
      this.onFinish?.(next);
    }
    this.processing = false;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
