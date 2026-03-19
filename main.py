"""
NV4000(지폐) + SMART Coin(동전) 통합 결제 게이트웨이
두 장치가 COM4 단일 포트, SSP 주소로 구분 (NV4000=0, SMART Coin=16)

실행: uvicorn main:app --port 8001
"""
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

import config
import itl_client
import payment_service
import state
from schemas import PaymentStartRequest, PaymentStatusResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.HTTP = httpx.AsyncClient(timeout=30.0)
    yield
    if state.ACTIVE_TX_TASK and not state.ACTIVE_TX_TASK.done():
        state.ACTIVE_TX_TASK.cancel()
        try:
            await state.ACTIVE_TX_TASK
        except asyncio.CancelledError:
            pass
    for device_id in [state.NV_DEVICE_ID, state.COIN_DEVICE_ID]:
        if device_id:
            await itl_client.disconnect(device_id)
    await state.HTTP.aclose()


app = FastAPI(title="NV4000 + SMART Coin 결제 게이트웨이", version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _err(e: Exception) -> str:
    if isinstance(e, HTTPException):
        return str(e.detail)
    etype = type(e).__name__
    msg = str(e)
    return f"{etype}: {msg}" if msg else etype


# ── 두 장치 순차 연결 ────────────────────────────────────

@app.post("/device/connect", tags=["장치"])
async def both_connect() -> dict[str, Any]:
    """
    NV4000 → SMART Coin 순차 연결.
    IF17 멀티드롭(COM4 공유): NV4000이 SerialPort를 먼저 열면
    ITL 서버가 이미 열린 포트를 재사용하여 SMART Coin 추가 연결 가능.
    동시(gather) 방식은 두 스레드가 SerialPort 생성 경쟁 → UnauthorizedAccessException.
    """
    nv_ok = False
    coin_ok = False
    nv_err = None
    coin_err = None

    # 1단계: NV4000 연결
    try:
        nv_result = await itl_client.open_nv4000()
        nv_id = nv_result.get("deviceID") or nv_result.get("deviceId") or ""
        async with state.STATE_LOCK:
            state.NV_DEVICE_ID = nv_id
        nv_ok = True
    except Exception as e:
        nv_err = _err(e)

    # 2단계: SMART Coin 연결 (NV4000이 COM4를 열었으므로 재사용 기대)
    try:
        coin_result = await itl_client.open_smart_coin()
        coin_id = coin_result.get("deviceID") or coin_result.get("deviceId") or ""
        async with state.STATE_LOCK:
            state.COIN_DEVICE_ID = coin_id
        coin_ok = True
    except Exception as e:
        coin_err = _err(e)

    return {
        "nv4000": {
            "ok": nv_ok,
            "device_id": state.NV_DEVICE_ID,
            "error": nv_err,
        },
        "smart_coin": {
            "ok": coin_ok,
            "device_id": state.COIN_DEVICE_ID,
            "error": coin_err,
        },
    }


@app.delete("/device/connect", tags=["장치"])
async def both_disconnect() -> dict[str, Any]:
    """NV4000 + SMART Coin 동시 연결 해제."""
    await asyncio.gather(
        itl_client.disconnect(state.NV_DEVICE_ID) if state.NV_DEVICE_ID else asyncio.sleep(0),
        itl_client.disconnect(state.COIN_DEVICE_ID) if state.COIN_DEVICE_ID else asyncio.sleep(0),
    )
    async with state.STATE_LOCK:
        state.NV_DEVICE_ID = None
        state.COIN_DEVICE_ID = None
    return {"ok": True, "message": "두 장치 연결 해제 완료"}


# ── NV4000 ────────────────────────────────────────────────

@app.post("/device/nv4000/connect", tags=["장치"])
async def nv4000_connect() -> dict[str, Any]:
    """
    NV4000 지폐 검증기 연결 (COM4, SSP 0).
    1) 이미 연결된 상태면 즉시 반환
    2) ITL 서버에 기존 열린 연결 있으면 재사용
    3) OpenConnection 호출
    """
    if state.NV_DEVICE_ID:
        return {"ok": True, "message": "이미 연결됨", "device_id": state.NV_DEVICE_ID}

    try:
        existing = await itl_client._find_existing(config.NV4000_COM, "nv4000")
    except Exception:
        existing = None

    if existing:
        async with state.STATE_LOCK:
            state.NV_DEVICE_ID = existing
        return {"ok": True, "device_id": existing, "reused": True}

    try:
        data = await itl_client.open_nv4000()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"NV4000 연결 실패: {_err(e)}")

    nv_id = data.get("deviceID") or data.get("deviceId") or ""
    if not nv_id:
        raise HTTPException(status_code=503, detail=f"deviceID 없음: {data}")
    async with state.STATE_LOCK:
        state.NV_DEVICE_ID = nv_id
    return {"ok": True, "device_id": nv_id, "com_port": config.NV4000_COM}


@app.delete("/device/nv4000/connect", tags=["장치"])
async def nv4000_disconnect() -> dict[str, Any]:
    nv_id = state.NV_DEVICE_ID
    if not nv_id:
        return {"ok": True, "message": "연결된 장치 없음"}
    await itl_client.disconnect(nv_id)
    async with state.STATE_LOCK:
        state.NV_DEVICE_ID = None
    return {"ok": True, "message": "NV4000 연결 해제"}


# ── SMART Coin ────────────────────────────────────────────

@app.post("/device/coin/connect", tags=["장치"])
async def coin_connect() -> dict[str, Any]:
    """
    SMART Coin 동전 투입기 연결 (COM4, SSP 16).
    1) 이미 연결된 상태면 즉시 반환
    2) ITL 서버에 기존 열린 연결 있으면 재사용
    3) OpenConnection 호출
    """
    if state.COIN_DEVICE_ID:
        return {"ok": True, "message": "이미 연결됨", "device_id": state.COIN_DEVICE_ID}

    try:
        existing = await itl_client._find_existing(config.COIN_COM, "smart_coin")
    except Exception:
        existing = None

    if existing:
        async with state.STATE_LOCK:
            state.COIN_DEVICE_ID = existing
        return {"ok": True, "device_id": existing, "reused": True}

    try:
        data = await itl_client.open_smart_coin()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"SMART Coin 연결 실패: {_err(e)}")

    coin_id = data.get("deviceID") or data.get("deviceId") or ""
    if not coin_id:
        raise HTTPException(status_code=503, detail=f"deviceID 없음: {data}")
    async with state.STATE_LOCK:
        state.COIN_DEVICE_ID = coin_id
    return {"ok": True, "device_id": coin_id, "com_port": config.COIN_COM}


@app.delete("/device/coin/connect", tags=["장치"])
async def coin_disconnect() -> dict[str, Any]:
    coin_id = state.COIN_DEVICE_ID
    if not coin_id:
        return {"ok": True, "message": "연결된 장치 없음"}
    await itl_client.disconnect(coin_id)
    async with state.STATE_LOCK:
        state.COIN_DEVICE_ID = None
    return {"ok": True, "message": "SMART Coin 연결 해제"}


# ── 장치 상태 ─────────────────────────────────────────────

@app.get("/device/status", tags=["장치"])
async def device_status() -> dict[str, Any]:
    return {
        "nv4000": {
            "connected": state.NV_DEVICE_ID is not None,
            "device_id": state.NV_DEVICE_ID,
            "com_port": config.NV4000_COM,
            "ssp": config.NV4000_SSP,
        },
        "smart_coin": {
            "connected": state.COIN_DEVICE_ID is not None,
            "device_id": state.COIN_DEVICE_ID,
            "com_port": config.COIN_COM,
            "ssp": config.COIN_SSP,
        },
    }


# ── 결제 ──────────────────────────────────────────────────

@app.post("/payment/start", tags=["결제"])
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


@app.get("/payment/status/{tx_id}", tags=["결제"])
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


@app.get("/payment/current", tags=["결제"])
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


@app.post("/payment/cancel/{tx_id}", tags=["결제"])
async def payment_cancel(tx_id: str) -> dict[str, Any]:
    cancelled = await payment_service.cancel_payment(tx_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"취소할 거래 '{tx_id}'가 없습니다")
    return {"ok": True, "tx_id": tx_id, "status": "CANCELLED"}


# ── NV4000 재고 ───────────────────────────────────────────

class LevelItem(BaseModel):
    value: int       # 권종 (센타보 단위, 예: 20000 = 200페소)
    amount: int      # 장수


@app.post("/device/nv4000/levels", tags=["장치"])
async def nv4000_set_levels(levels: list[LevelItem]) -> dict[str, Any]:
    """
    NV4000 리사이클러 권종별 재고 설정.
    예) [{"value": 20000, "amount": 5}, {"value": 10000, "amount": 3}]
    """
    if not state.NV_DEVICE_ID:
        raise HTTPException(status_code=409, detail="NV4000이 연결되지 않았습니다")
    results = []
    for item in levels:
        try:
            res = await itl_client.set_denomination_level(
                state.NV_DEVICE_ID, item.value, config.NV4000_CURRENCY, item.amount
            )
            results.append({"value": item.value, "amount": item.amount, "ok": True, "detail": res})
        except Exception as e:
            results.append({"value": item.value, "amount": item.amount, "ok": False, "error": _err(e)})
    return {"results": results}


@app.get("/device/nv4000/levels", tags=["장치"])
async def nv4000_get_levels() -> dict[str, Any]:
    """NV4000 권종별 재고 조회."""
    if not state.NV_DEVICE_ID:
        raise HTTPException(status_code=409, detail="NV4000이 연결되지 않았습니다")
    levels = await itl_client.get_all_levels(state.NV_DEVICE_ID)
    return {"device_id": state.NV_DEVICE_ID, "levels": levels}


# ── 헬스체크 ──────────────────────────────────────────────

@app.get("/health", tags=["시스템"])
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "nv4000_connected": state.NV_DEVICE_ID is not None,
        "coin_connected": state.COIN_DEVICE_ID is not None,
        "active_tx": state.ACTIVE_TX.get("tx_id") if state.ACTIVE_TX else None,
    }
