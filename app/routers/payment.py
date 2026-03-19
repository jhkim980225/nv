"""결제 라우터 — /payment/*"""
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models import state
from app.models.schemas import PaymentStartRequest, PaymentStatusResponse
from app.services import payment_service

router = APIRouter(prefix="/payment", tags=["결제"])


@router.post("/start")
async def payment_start(req: PaymentStartRequest) -> dict[str, Any]:
    try:
        tx_id = await payment_service.start_payment(req.target_amount, req.timeout_sec)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"거래 시작 실패: {e}")
    return {
        "tx_id": tx_id,
        "target_amount": req.target_amount,
        "timeout_sec": req.timeout_sec,
        "message": "지폐 또는 동전을 투입하세요",
    }


@router.get("/status/{tx_id}")
async def payment_status(tx_id: str) -> PaymentStatusResponse:
    tx = state.ACTIVE_TX
    if not tx or tx.get("tx_id") != tx_id:
        raise HTTPException(status_code=404, detail=f"거래 '{tx_id}'를 찾을 수 없습니다")
    total = tx["bill_amount"] + tx["coin_amount"]
    return PaymentStatusResponse(
        tx_id=tx_id,
        status=tx["status"],
        target_amount=tx["target_amount"],
        bill_amount=tx["bill_amount"],
        coin_amount=tx["coin_amount"],
        total_amount=total,
        remaining_amount=max(0, tx["target_amount"] - total),
        change_amount=tx["change_amount"],
        change_result=tx.get("change_result"),
        events=tx["events"][-50:],
        elapsed_sec=round(time.time() - tx["started_at"], 1),
        error=tx.get("error"),
    )


@router.get("/current")
async def payment_current() -> dict[str, Any]:
    tx = state.ACTIVE_TX
    if not tx:
        return {"active": False, "tx": None}
    total = tx["bill_amount"] + tx["coin_amount"]
    return {
        "active": tx.get("status") == "ACTIVE",
        "tx": {
            "tx_id": tx["tx_id"],
            "status": tx["status"],
            "target_amount": tx["target_amount"],
            "total_amount": total,
            "remaining_amount": max(0, tx["target_amount"] - total),
            "elapsed_sec": round(time.time() - tx["started_at"], 1),
        },
    }


@router.post("/cancel/{tx_id}")
async def payment_cancel(tx_id: str) -> dict[str, Any]:
    cancelled = await payment_service.cancel_payment(tx_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"취소할 거래 '{tx_id}'가 없습니다")
    return {"ok": True, "tx_id": tx_id, "status": "CANCELLED"}
