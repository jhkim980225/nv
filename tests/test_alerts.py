"""알람 엔드포인트 테스트"""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient

from app.models import state
from app.services.alert_service import _push_alert


# ── 알람 목록 조회 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_alerts_empty(client: AsyncClient):
    """/alerts — 알람 없을 때 빈 배열."""
    resp = await client.get("/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["alerts"] == []


@pytest.mark.asyncio
async def test_get_alerts_with_data(client: AsyncClient):
    """/alerts — 알람 최신순 반환."""
    _push_alert("LOW_COIN", "WARNING", "첫 번째 알람")
    _push_alert("DISPENSE_FAILED", "ERROR", "두 번째 알람")
    resp = await client.get("/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["alerts"][0]["message"] == "두 번째 알람"  # 최신순


@pytest.mark.asyncio
async def test_get_alerts_unacked_only(client: AsyncClient):
    """/alerts?unacked_only=true — 미확인 알람만."""
    a = _push_alert("LOW_COIN", "WARNING", "미확인")
    b = _push_alert("LOW_COIN", "WARNING", "확인됨")
    b["acknowledged"] = True
    resp = await client.get("/alerts?unacked_only=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["alerts"][0]["alert_id"] == a["alert_id"]


# ── 알람 확인 처리 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_ack_alert_success(client: AsyncClient):
    """/alerts/{id}/ack — 확인 처리."""
    alert = _push_alert("LOW_COIN", "WARNING", "테스트 알람")
    resp = await client.post(f"/alerts/{alert['alert_id']}/ack")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert state.ALERTS[-1]["acknowledged"] is True


@pytest.mark.asyncio
async def test_ack_alert_not_found(client: AsyncClient):
    """/alerts/{id}/ack — 없는 알람 404."""
    resp = await client.post("/alerts/nonexistent/ack")
    assert resp.status_code == 404


# ── 알람 삭제 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_alerts(client: AsyncClient):
    """/alerts DELETE — 확인된 알람 삭제."""
    a = _push_alert("LOW_COIN", "WARNING", "미확인")
    b = _push_alert("LOW_COIN", "WARNING", "확인됨")
    b["acknowledged"] = True
    resp = await client.delete("/alerts")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1
    assert len(state.ALERTS) == 1
    assert state.ALERTS[0]["alert_id"] == a["alert_id"]


# ── 수동 재고 점검 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_check_no_devices(client: AsyncClient):
    """/alerts/check — 장치 미연결 시 알람 없음."""
    resp = await client.post("/alerts/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_alerts"] == 0


@pytest.mark.asyncio
async def test_manual_check_empty_coin(client: AsyncClient):
    """/alerts/check — 동전 재고 없을 때 알람 생성."""
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    state.NV_DEVICE_ID = None
    mock_levels = [
        {"Value": 100, "storedInPayout": 0},
        {"Value": 500, "storedInPayout": 5},
    ]
    with patch("app.services.itl_client.get_all_levels", new=AsyncMock(return_value=mock_levels)):
        resp = await client.post("/alerts/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_alerts"] >= 1
    assert any("1페소" in a["message"] for a in data["coin_alerts"])


@pytest.mark.asyncio
async def test_manual_check_empty_recycler(client: AsyncClient):
    """/alerts/check — 리사이클러 비었을 때 알람 생성."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    state.COIN_DEVICE_ID = None
    mock_levels = [
        {"Value": 10000, "AcceptRoute": "RECYCLER_1", "StoredInPayout": 0},
        {"Value": 5000,  "AcceptRoute": "RECYCLER_2", "StoredInPayout": 3},
    ]
    with patch("app.services.itl_client.get_all_levels", new=AsyncMock(return_value=mock_levels)):
        resp = await client.post("/alerts/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_alerts"] == 1
    assert "1번 리사이클러" in data["nv_alerts"][0]["message"]
    assert "100페소" in data["nv_alerts"][0]["message"]


# ── SSE 스트림 ────────────────────────────────────────────

def test_alerts_stream_state():
    """SSE 스트림 — 미확인 알람이 올바르게 state에 저장되는지 확인.
    (무한 루프 SSE는 ASGI 환경에서 직접 테스트 불가 → 상태 검증으로 대체)
    """
    _push_alert("LOW_COIN", "ERROR", "긴급 알람")
    _push_alert("LOW_COIN", "WARNING", "경고 알람")
    state.ALERTS[0]["acknowledged"] = True  # 첫 번째 확인 처리

    unacked = [a for a in state.ALERTS if not a.get("acknowledged")]
    assert len(unacked) == 1
    assert "경고 알람" in unacked[0]["message"]


# ── alert_service 단위 테스트 ─────────────────────────────

@pytest.mark.asyncio
async def test_check_coin_inventory_insufficient(client: AsyncClient):
    """check_coin_inventory — 부족 시 ERROR 알람 생성."""
    from app.services.alert_service import check_coin_inventory
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    mock_levels = [
        {"Value": 100, "storedInPayout": 2},   # 200 available
        {"Value": 500, "storedInPayout": 0},   # empty
    ]
    with patch("app.services.itl_client.get_all_levels", new=AsyncMock(return_value=mock_levels)):
        alerts = await check_coin_inventory(5000)  # need 50 pesos
    error_alerts = [a for a in alerts if a["severity"] == "ERROR"]
    assert len(error_alerts) == 1
    assert "50페소" in error_alerts[0]["message"]


@pytest.mark.asyncio
async def test_check_nv4000_recycler(client: AsyncClient):
    """check_nv4000_recycler_inventory — 빈 리사이클러 알람."""
    from app.services.alert_service import check_nv4000_recycler_inventory
    state.NV_DEVICE_ID = "NV4000-COM4"
    mock_levels = [
        {"Value": 20000, "AcceptRoute": "RECYCLER_1", "StoredInPayout": 0},
        {"Value": 10000, "AcceptRoute": "RECYCLER_2", "StoredInPayout": 2},
        {"Value": 5000,  "AcceptRoute": "RECYCLER_3", "StoredInPayout": 0},
        {"Value": 2000,  "AcceptRoute": "CASHBOX",    "StoredInPayout": 5},
    ]
    with patch("app.services.itl_client.get_all_levels", new=AsyncMock(return_value=mock_levels)):
        alerts = await check_nv4000_recycler_inventory()
    assert len(alerts) == 2
    messages = [a["message"] for a in alerts]
    assert any("1번 리사이클러" in m and "200페소" in m for m in messages)
    assert any("3번 리사이클러" in m and "50페소"  in m for m in messages)


def test_alert_dispense_failed(client: AsyncClient):
    """alert_dispense_failed — DISPENSE_FAILED 알람 생성."""
    from app.services.alert_service import alert_dispense_failed
    alert = alert_dispense_failed(3000, {"error": "coin fail"}, "nv fail")
    assert alert["type"] == "DISPENSE_FAILED"
    assert alert["severity"] == "ERROR"
    assert "30페소" in alert["message"]
    assert len(state.ALERTS) == 1
