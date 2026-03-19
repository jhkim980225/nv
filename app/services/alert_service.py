"""
알람 서비스 — 재고 부족 및 지급 실패 감지 후 알람 생성
"""
import time
import uuid
from typing import Any

from app.models import state
from app.services import itl_client


def _lv_get(lv: dict, *keys: str, default=0):
    """PascalCase / camelCase 둘 다 지원."""
    for k in keys:
        if k in lv:
            return lv[k]
    return default


def _push_alert(alert_type: str, severity: str, message: str, detail: dict | None = None) -> dict:
    alert = {
        "alert_id": uuid.uuid4().hex,
        "type": alert_type,
        "severity": severity,
        "message": message,
        "detail": detail or {},
        "created_at": time.time(),
        "acknowledged": False,
    }
    state.ALERTS.append(alert)
    if len(state.ALERTS) > state.ALERTS_MAX:
        state.ALERTS.pop(0)
    return alert


def _recycler_num(route: str) -> str:
    """'RECYCLER_2' → '2',  'RECYCLER' → '1'."""
    if "_" in route:
        return route.split("_")[-1]
    return "1"


# ── 동전 재고 점검 ────────────────────────────────────────

async def check_coin_inventory(change_needed: int = 0) -> list[dict]:
    """
    SMART Coin 재고 확인.
    - change_needed > 0 : 해당 금액 지급 불가 시 ERROR 알람
    - change_needed = 0 : 빈 권종만 WARNING 알람
    """
    if not state.COIN_DEVICE_ID:
        return []
    try:
        levels = await itl_client.get_all_levels(state.COIN_DEVICE_ID)
    except Exception:
        return []

    alerts = []
    total_available = 0
    empty_denoms: list[str] = []

    for lv in levels:
        value  = _lv_get(lv, "Value", "value")
        stored = _lv_get(lv, "StoredInPayout", "storedInPayout")
        total_available += stored * value
        if stored == 0 and value > 0:
            empty_denoms.append(f"{value // 100}페소")

    # 빈 권종 경고
    for denom in empty_denoms:
        alerts.append(_push_alert(
            "LOW_COIN", "WARNING",
            f"동전을 충전해야합니다 — {denom} 동전이 비어있습니다",
            {"denomination": denom},
        ))

    # 지급 불가 오류
    if change_needed > 0 and total_available < change_needed:
        alerts.append(_push_alert(
            "LOW_COIN", "ERROR",
            f"동전을 충전해야합니다 "
            f"(필요: {change_needed // 100}페소, 가용: {total_available // 100}페소)",
            {"change_needed": change_needed, "total_available": total_available},
        ))

    return alerts


# ── NV4000 리사이클러 재고 점검 ──────────────────────────

async def check_nv4000_recycler_inventory() -> list[dict]:
    """NV4000 리사이클러 재고 확인 — 비어있는 리사이클러마다 알람 생성."""
    if not state.NV_DEVICE_ID:
        return []
    try:
        levels = await itl_client.get_all_levels(state.NV_DEVICE_ID)
    except Exception:
        return []

    alerts = []
    for lv in levels:
        route  = _lv_get(lv, "AcceptRoute", "acceptRoute", default="")
        stored = _lv_get(lv, "StoredInPayout", "storedInPayout")
        value  = _lv_get(lv, "Value", "value")

        if "RECYCLER" not in route or stored > 0:
            continue

        num   = _recycler_num(route)
        pesos = value // 100
        alerts.append(_push_alert(
            "LOW_BILL_RECYCLER", "WARNING",
            f"{num}번 리사이클러에 {pesos}페소 지폐를 추가해야합니다",
            {"recycler": route, "recycler_num": num, "denomination_value": value, "denomination_pesos": pesos},
        ))

    return alerts


# ── 지급 실패 알람 ────────────────────────────────────────

def alert_dispense_failed(change: int, coin_error: Any, nv_error: Any) -> dict:
    """동전 + 지폐 모두 지급 실패 시 ERROR 알람."""
    return _push_alert(
        "DISPENSE_FAILED", "ERROR",
        f"거스름돈 {change // 100}페소 지급 실패 — 동전과 지폐 모두 지급 불가",
        {"change": change, "coin_error": str(coin_error), "nv_error": str(nv_error)},
    )


# ── 전체 재고 점검 (수동) ─────────────────────────────────

async def check_all_inventory() -> dict:
    """동전 + NV4000 리사이클러 전체 재고 점검."""
    import asyncio
    coin_alerts, nv_alerts = await asyncio.gather(
        check_coin_inventory(0),
        check_nv4000_recycler_inventory(),
        return_exceptions=True,
    )
    coin_alerts = coin_alerts if isinstance(coin_alerts, list) else []
    nv_alerts   = nv_alerts   if isinstance(nv_alerts,   list) else []
    return {
        "new_alerts": len(coin_alerts) + len(nv_alerts),
        "coin_alerts": coin_alerts,
        "nv_alerts": nv_alerts,
    }
