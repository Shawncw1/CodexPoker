from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from codexpoker_backend.api.deps import engine_service
from codexpoker_backend.engine.models import (
    HandHistory,
    ReplayResult,
    StartHandResponse,
    SubmitActionRequest,
    SubmitActionResponse,
    TableConfig,
    ViewState,
)
from codexpoker_backend.engine.service import EngineRejectedAction


router = APIRouter(prefix="/api")


class CreateTableRequest(BaseModel):
    config: TableConfig = TableConfig()


class CreateTableResponse(BaseModel):
    table_id: str
    start: StartHandResponse


class ReplayRequest(BaseModel):
    hand_history: dict


async def _auto_advance_to_human(table_id: str, loops: int = 4) -> list:
    all_events = []
    for _ in range(loops):
        view = await engine_service.get_view_state(table_id)
        if view.hand_id is not None or view.session_outcome.value != "running":
            break
        start = await engine_service.start_new_hand(table_id)
        all_events.extend(start.event_queue)
        _, bot_events = await engine_service.run_bots_until_human_turn(table_id)
        all_events.extend(bot_events)
    return all_events


@router.post("/tables", response_model=CreateTableResponse)
async def create_table(request: CreateTableRequest) -> CreateTableResponse:
    table_id = await engine_service.create_table(request.config)
    start = await engine_service.start_new_hand(table_id)
    _, bot_events = await engine_service.run_bots_until_human_turn(table_id)
    start.event_queue.extend(bot_events)
    start.event_queue.extend(await _auto_advance_to_human(table_id))
    refreshed = await engine_service.get_view_state(table_id)
    start.view_state = refreshed
    return CreateTableResponse(table_id=table_id, start=start)


@router.post("/tables/{table_id}/start", response_model=StartHandResponse)
async def start_hand(table_id: str) -> StartHandResponse:
    try:
        start = await engine_service.start_new_hand(table_id)
        return start
    except EngineRejectedAction as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/tables/{table_id}/view", response_model=ViewState)
async def get_view(table_id: str) -> ViewState:
    return await engine_service.get_view_state(table_id)


@router.get("/tables/{table_id}/allowed-actions")
async def get_allowed_actions(table_id: str) -> dict:
    allowed = await engine_service.get_allowed_actions(table_id)
    return allowed.model_dump(mode="json")


@router.post("/tables/{table_id}/actions", response_model=SubmitActionResponse)
async def submit_action(table_id: str, request: SubmitActionRequest) -> SubmitActionResponse:
    try:
        response = await engine_service.submit_action(
            table_id=table_id,
            viewer_id="human",
            action=request.action,
            action_seq=request.action_seq,
            idempotency_key=request.idempotency_key,
            amount_to=request.amount_to,
        )
        _, bot_events = await engine_service.run_bots_until_human_turn(table_id)
        if bot_events:
            response.event_queue_delta.extend(bot_events)
        response.event_queue_delta.extend(await _auto_advance_to_human(table_id))
        response.view_state = await engine_service.get_view_state(table_id)
        response.server_action_seq = await engine_service.get_server_action_seq(table_id)
        return response
    except EngineRejectedAction as exc:
        view_state = await engine_service.get_view_state(table_id)
        return SubmitActionResponse(
            accepted=False,
            error={"code": exc.code, "message": exc.message},
            view_state=view_state,
            event_queue_delta=[],
            server_action_seq=0,
        )


@router.post("/tables/{table_id}/restart", response_model=StartHandResponse)
async def restart_session(table_id: str) -> StartHandResponse:
    restart = await engine_service.restart_session(table_id)
    _, bot_events = await engine_service.run_bots_until_human_turn(table_id)
    restart.event_queue.extend(bot_events)
    restart.event_queue.extend(await _auto_advance_to_human(table_id))
    restart.view_state = await engine_service.get_view_state(table_id)
    return restart


@router.get("/tables/{table_id}/hands/{hand_id}/history")
async def export_history(table_id: str, hand_id: int, mode: str = "viewer") -> dict:
    try:
        return await engine_service.export_hand_history(table_id, hand_id, mode)
    except EngineRejectedAction as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/replay", response_model=ReplayResult)
async def replay(request: ReplayRequest) -> ReplayResult:
    history = HandHistory.model_validate(request.hand_history)
    return await engine_service.replay_hand_history(history.model_dump(mode="json"))


@router.websocket("/ws/tables/{table_id}")
async def table_socket(websocket: WebSocket, table_id: str) -> None:
    await websocket.accept()
    try:
        queue = await engine_service.subscribe(table_id)
    except KeyError:
        await websocket.close(code=1008)
        return

    try:
        view = await engine_service.get_view_state(table_id)
        await websocket.send_json({"type": "VIEW_STATE", "payload": view.model_dump(mode="json")})
        while True:
            event = await queue.get()
            await websocket.send_json({"type": "EVENT", "payload": event.model_dump(mode="json")})
    except WebSocketDisconnect:
        pass
    finally:
        await engine_service.unsubscribe(table_id, queue)
