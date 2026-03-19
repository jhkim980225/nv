"""알람 라우터 — /alerts/*"""
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models import state
from app.services import alert_service

router = APIRouter(prefix="/alerts", tags=["알람"])


@router.get("/stream")
async def alerts_stream() -> StreamingResponse:
    """
    SSE 알람 스트림.
    구독 즉시 미확인 알람 전송 → 이후 새 알람 발생 시 실시간 전송.
    """
    async def generator():
        # 기존 미확인 알람 즉시 전송
        for alert in state.ALERTS:
            if not alert.get("acknowledged"):
                yield f"data: {json.dumps(alert, ensure_ascii=False)}\n\n"

        last_idx = len(state.ALERTS)
        while True:
            await asyncio.sleep(0.5)
            if len(state.ALERTS) > last_idx:
                for alert in state.ALERTS[last_idx:]:
                    yield f"data: {json.dumps(alert, ensure_ascii=False)}\n\n"
                last_idx = len(state.ALERTS)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("")
async def get_alerts(unacked_only: bool = False) -> dict[str, Any]:
    """알람 목록 조회 (최신순). unacked_only=true 시 미확인만."""
    alerts = state.ALERTS
    if unacked_only:
        alerts = [a for a in alerts if not a.get("acknowledged")]
    return {"count": len(alerts), "alerts": list(reversed(alerts))}


@router.post("/check")
async def manual_check() -> dict[str, Any]:
    """수동 전체 재고 점검 — 부족한 항목 알람 생성."""
    return await alert_service.check_all_inventory()


@router.post("/{alert_id}/ack")
async def ack_alert(alert_id: str) -> dict[str, Any]:
    """알람 확인 처리."""
    for alert in state.ALERTS:
        if alert["alert_id"] == alert_id:
            alert["acknowledged"] = True
            return {"ok": True, "alert_id": alert_id}
    raise HTTPException(status_code=404, detail=f"알람 '{alert_id}'를 찾을 수 없습니다")


@router.delete("")
async def clear_alerts() -> dict[str, Any]:
    """확인된 알람 삭제."""
    before = len(state.ALERTS)
    state.ALERTS = [a for a in state.ALERTS if not a.get("acknowledged")]
    return {"ok": True, "removed": before - len(state.ALERTS)}
