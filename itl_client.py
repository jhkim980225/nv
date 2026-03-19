"""
ITL REST API 클라이언트 — coconut 프로젝트 검증된 패턴 기반
"""
import time
from typing import Any, Optional

import httpx
from fastapi import HTTPException

import config
import state


def _require_http() -> httpx.AsyncClient:
    if not state.HTTP:
        raise HTTPException(status_code=503, detail="HTTP 클라이언트가 준비되지 않았습니다")
    return state.HTTP


def _url(path: str) -> str:
    """ITL API full URL 생성."""
    return f"{config.ITL_BASE_URL}{path}"


async def _authenticate() -> str:
    client = _require_http()
    try:
        r = await client.post(
            _url("/api/Users/Authenticate"),
            json={"Username": config.ITL_USERNAME, "Password": config.ITL_PASSWORD},
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"ITL 서버 연결 실패: {type(e).__name__}: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=401, detail="ITL 인증 실패")
    data = r.json()
    token_type = data.get("token_type") or data.get("tokenType", "Bearer")
    token = data.get("token")
    if not token:
        raise HTTPException(status_code=502, detail="ITL 인증 응답에 token이 없습니다")
    state.AUTH_HEADER = f"{token_type} {token}"
    return state.AUTH_HEADER


async def _itl_request(method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
    """ITL API 요청 래퍼 — 401 시 재인증 후 1회 재시도."""
    client = _require_http()
    if not state.AUTH_HEADER:
        await _authenticate()

    async def _send() -> httpx.Response:
        return await client.request(
            method, _url(path),
            params=params,
            json=json,
            headers={"Authorization": state.AUTH_HEADER} if state.AUTH_HEADER else {},
        )

    try:
        r = await _send()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"ITL 서버 연결 실패: {type(e).__name__}: {e}")

    if r.status_code == 401:
        await _authenticate()
        try:
            r = await _send()
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"ITL 서버 연결 실패: {type(e).__name__}: {e}")

    if r.status_code >= 400:
        try:
            msg = r.json()
        except Exception:
            msg = r.text
        raise HTTPException(status_code=r.status_code, detail=msg)

    state.LAST_ITL_OK_AT = time.time()

    if not r.content:
        return None
    try:
        return r.json()
    except Exception:
        return r.text


def _is_real_error(v: Any) -> bool:
    """실제 오류 값인지 확인 (None/"NONE" 제외)."""
    if v is None:
        return False
    s = str(v).strip()
    return bool(s) and s.upper() != "NONE"


def _looks_like_not_found(detail: Any) -> bool:
    s = str(detail).lower()
    return "cash device not found" in s or "device not found" in s


# ── 장치 연결 ─────────────────────────────────────────────

async def get_all_cash_devices() -> list[str]:
    """ITL 서버에 현재 열린 deviceID 목록 조회."""
    data = await _itl_request("GET", "/api/CashDevice/GetAllCashDevices")
    if not isinstance(data, list):
        return []
    return [str(x) for x in data if x]


async def _find_existing(com_port: str, keyword: str) -> Optional[str]:
    """
    ITL 서버에서 이미 열린 deviceID 중 com_port + keyword 모두 매칭하는 것 탐색.
    같은 COM 포트에 여러 장치(SSP 주소 다름)가 있을 수 있으므로 keyword 필수 매칭.
    """
    device_ids = await get_all_cash_devices()
    port = com_port.strip().lower()
    kw = keyword.strip().lower()
    for did in device_ids:
        d = did.lower()
        if port in d and kw in d:
            return did
    return None


async def _open_device(body: dict) -> dict[str, Any]:
    """OpenConnection 공통 처리 — 응답 검증 포함."""
    data = await _itl_request("POST", "/api/CashDevice/OpenConnection", json=body)
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="OpenConnection 응답 형식 오류")
    device_id = data.get("deviceID") or data.get("deviceId") or ""
    is_open = data.get("isOpen")
    open_result = data.get("openResult", "")
    error = data.get("error") or data.get("deviceError") or ""
    if device_id and is_open is True and not _is_real_error(error):
        return data
    if open_result and str(open_result).upper() not in ("SUCCESS", "OK"):
        raise HTTPException(status_code=409, detail=f"OpenConnection 실패: {open_result}, error={error}")
    if _is_real_error(error):
        raise HTTPException(status_code=409, detail=f"COM 포트 오류: {error}")
    raise HTTPException(status_code=409, detail=f"OpenConnection 실패 (응답: {data})")


async def open_nv4000() -> dict[str, Any]:
    """NV4000 지폐 검증기 연결 (camelCase body, coconut 검증 패턴).
    리사이클러 라우팅: 200페소→1번, 100페소→2번, 50페소→3번, 20페소→4번.
    """
    currency = config.NV4000_CURRENCY
    return await _open_device({
        "comPort": config.NV4000_COM,
        "sspAddress": config.NV4000_SSP,
        "enableAcceptor": True,
        "enableAutoAcceptEscrow": True,
        "setRoutes": [
            {"Denomination": f"20000 {currency}", "Route": 1},  # 200페소 → 리사이클러 1
            {"Denomination": f"10000 {currency}", "Route": 2},  # 100페소 → 리사이클러 2
            {"Denomination": f"5000 {currency}",  "Route": 3},  # 50페소  → 리사이클러 3
            {"Denomination": f"2000 {currency}",  "Route": 4},  # 20페소  → 리사이클러 4
        ],
    })


async def open_smart_coin() -> dict[str, Any]:
    """SMART Coin 동전 투입기 연결."""
    denominations = config.COIN_DENOMINATIONS
    currency = config.COIN_CURRENCY
    inhibits = [{"Denomination": f"{d} {currency}", "Inhibit": False} for d in denominations]
    routes = [{"Denomination": f"{d} {currency}", "Route": 7} for d in denominations]
    return await _open_device({
        "comPort": config.COIN_COM,
        "sspAddress": config.COIN_SSP,
        "currency": currency,
        "denominations": denominations,
        "setInhibits": inhibits,
        "setRoutes": routes,
        "enableAcceptor": True,
        "enablePayout": True,
    })

    if _is_real_error(error):
        raise HTTPException(status_code=409, detail=f"COM 포트 오류: {error}")

    raise HTTPException(status_code=409, detail=f"OpenConnection 실패 (응답: {data})")


async def disconnect(device_id: str) -> None:
    """장치 연결 해제 — DisconnectDevice + CloseDevice 모두 시도 (오류 무시)."""
    for path in ("/api/CashDevice/DisconnectDevice", "/api/CashDevice/CloseDevice"):
        try:
            await _itl_request("POST", path, params={"deviceID": device_id})
        except Exception:
            pass


# ── 수락기 제어 ───────────────────────────────────────────

async def enable_acceptor(device_id: str) -> None:
    await _itl_request("POST", "/api/CashDevice/EnableAcceptor", params={"deviceID": device_id})


async def disable_acceptor(device_id: str) -> None:
    try:
        await _itl_request("POST", "/api/CashDevice/DisableAcceptor", params={"deviceID": device_id})
    except Exception:
        pass


# ── SMART Coin 전용 ───────────────────────────────────────

async def enable_coin_mech(device_id: str) -> None:
    await _itl_request("POST", "/api/CashDevice/EnableCoinMechOrFeeder", params={"deviceID": device_id}, json=True)


async def disable_coin_mech(device_id: str) -> None:
    try:
        await _itl_request("POST", "/api/CashDevice/EnableCoinMechOrFeeder", params={"deviceID": device_id}, json=False)
    except Exception:
        pass


# ── 상태 폴링 ─────────────────────────────────────────────

async def get_device_status(device_id: str) -> list[dict]:
    result = await _itl_request("GET", "/api/CashDevice/GetDeviceStatus", params={"deviceID": device_id})
    return result if isinstance(result, list) else []


# ── 거스름돈 지급 ─────────────────────────────────────────

async def enable_payout(device_id: str) -> None:
    await _itl_request("POST", "/api/CashDevice/EnablePayout", params={"deviceID": device_id}, json=0)


async def dispense_value(device_id: str, value: int, currency: str) -> dict:
    result = await _itl_request(
        "POST",
        "/api/CashDevice/DispenseValue",
        params={"deviceID": device_id},
        json={"Value": value, "CountryCode": currency},
    )
    return result if isinstance(result, dict) else {"dispenseResult": str(result)}


# ── 재고 조회 ─────────────────────────────────────────────

async def get_all_levels(device_id: str) -> list[dict]:
    result = await _itl_request("GET", "/api/CashDevice/GetAllLevels", params={"deviceID": device_id})
    return result if isinstance(result, list) else []


async def set_denomination_level(device_id: str, value: int, currency: str, amount: int) -> dict:
    result = await _itl_request(
        "POST",
        "/api/CashDevice/SetDenominationLevel",
        params={"deviceID": device_id},
        json={"Value": value, "CountryCode": currency, "Amount": amount},
    )
    return result if isinstance(result, dict) else {"result": str(result)}
