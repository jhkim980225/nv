"""장치 연결/제어 라우터 — /device/*"""
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from app import config
from app.models import state
from app.models.schemas import LevelItem
from app.services import itl_client

router = APIRouter(prefix="/device", tags=["장치"])


def _err(e: Exception) -> str:
    if isinstance(e, HTTPException):
        return str(e.detail)
    etype = type(e).__name__
    msg = str(e)
    return f"{etype}: {msg}" if msg else etype


# ── 두 장치 순차 연결 ────────────────────────────────────

@router.post("/connect")
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


@router.delete("/connect")
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

@router.post("/nv4000/connect")
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


@router.delete("/nv4000/connect")
async def nv4000_disconnect() -> dict[str, Any]:
    nv_id = state.NV_DEVICE_ID
    if not nv_id:
        return {"ok": True, "message": "연결된 장치 없음"}
    await itl_client.disconnect(nv_id)
    async with state.STATE_LOCK:
        state.NV_DEVICE_ID = None
    return {"ok": True, "message": "NV4000 연결 해제"}


# ── SMART Coin ────────────────────────────────────────────

@router.post("/coin/connect")
async def coin_connect() -> dict[str, Any]:
    """
    SMART Coin 동전 투입기 연결 (COM3, SSP 16).
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


@router.delete("/coin/connect")
async def coin_disconnect() -> dict[str, Any]:
    coin_id = state.COIN_DEVICE_ID
    if not coin_id:
        return {"ok": True, "message": "연결된 장치 없음"}
    await itl_client.disconnect(coin_id)
    async with state.STATE_LOCK:
        state.COIN_DEVICE_ID = None
    return {"ok": True, "message": "SMART Coin 연결 해제"}


# ── 장치 상태 ─────────────────────────────────────────────

@router.get("/status")
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


# ── NV4000 재고 ───────────────────────────────────────────

@router.post("/nv4000/levels")
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


@router.get("/nv4000/levels")
async def nv4000_get_levels() -> dict[str, Any]:
    """NV4000 권종별 재고 조회."""
    if not state.NV_DEVICE_ID:
        raise HTTPException(status_code=409, detail="NV4000이 연결되지 않았습니다")
    levels = await itl_client.get_all_levels(state.NV_DEVICE_ID)
    return {"device_id": state.NV_DEVICE_ID, "levels": levels}
