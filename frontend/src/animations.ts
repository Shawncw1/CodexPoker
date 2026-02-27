import type { EventEnvelope } from "./types";

export const Anim = {
  speed: 1,
  deal: {
    startPauseMs: 160,
    perCardMs: 170,
    staggerMs: 60,
  },
  chips: {
    toFrontMs: 240,
    settleMs: 90,
    toPotMs: 320,
  },
  badges: {
    holdMs: 900,
    fadeMs: 220,
  },
  board: {
    flopMs: 260,
    turnRiverMs: 220,
  },
  showdown: {
    revealMs: 260,
    awardMs: 360,
  },
  betweenHands: {
    pauseMinMs: 1000,
    pauseMaxMs: 1400,
  },
};

export function durationForEvent(event: EventEnvelope): number {
  switch (event.event_type) {
    case "POST_BLIND":
      return 260 / Anim.speed;
    case "DEAL_CARD":
      return 170 / Anim.speed;
    case "ACTION":
      return (240 + Anim.badges.holdMs) / Anim.speed;
    case "STREET_END_COLLECT":
      return 320 / Anim.speed;
    case "BOARD_REVEAL":
      return (event.payload.street === "flop" ? Anim.board.flopMs : Anim.board.turnRiverMs) / Anim.speed;
    case "SHOWDOWN_REVEAL":
      return Anim.showdown.revealMs / Anim.speed;
    case "POT_AWARD":
      return Anim.showdown.awardMs / Anim.speed;
    case "HAND_END":
      return Anim.betweenHands.pauseMinMs / Anim.speed;
    default:
      return 80 / Anim.speed;
  }
}
