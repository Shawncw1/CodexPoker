from __future__ import annotations

import argparse
import json
from pathlib import Path

from codexpoker_backend.api.deps import engine_service


async def _run(path: Path) -> None:
    history = json.loads(path.read_text())
    result = await engine_service.replay_hand_history(history)
    print(json.dumps(result.model_dump(mode="json"), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a saved hand_history.json")
    parser.add_argument("history_file", type=Path)
    args = parser.parse_args()

    import asyncio

    asyncio.run(_run(args.history_file))


if __name__ == "__main__":
    main()
