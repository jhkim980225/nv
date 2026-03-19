"""장치 엔드포인트 테스트"""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.models import state


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    """헬스체크 응답 확인."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "nv4000_connected" in data
    assert "coin_connected" in data


@pytest.mark.asyncio
async def test_device_status_disconnected(client: AsyncClient):
    """/device/status — 장치 미연결 시 connected=False."""
    resp = await client.get("/device/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nv4000"]["connected"] is False
    assert data["smart_coin"]["connected"] is False


@pytest.mark.asyncio
async def test_device_status_connected(client: AsyncClient):
    """/device/status — 장치 연결 후 connected=True."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    resp = await client.get("/device/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nv4000"]["connected"] is True
    assert data["nv4000"]["device_id"] == "NV4000-COM4"
    assert data["smart_coin"]["connected"] is True


@pytest.mark.asyncio
async def test_nv4000_connect_already_connected(client: AsyncClient):
    """/device/nv4000/connect — 이미 연결 시 200 즉시 반환."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    resp = await client.post("/device/nv4000/connect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "이미 연결됨" in data["message"]


@pytest.mark.asyncio
async def test_coin_connect_already_connected(client: AsyncClient):
    """/device/coin/connect — 이미 연결 시 200 즉시 반환."""
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    resp = await client.post("/device/coin/connect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "이미 연결됨" in data["message"]


@pytest.mark.asyncio
async def test_nv4000_disconnect_not_connected(client: AsyncClient):
    """/device/nv4000/connect DELETE — 미연결 시 ok 반환."""
    resp = await client.delete("/device/nv4000/connect")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_both_disconnect(client: AsyncClient):
    """/device/connect DELETE — 두 장치 연결 해제."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    with patch("app.services.itl_client.disconnect", new=AsyncMock()):
        resp = await client.delete("/device/connect")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert state.NV_DEVICE_ID is None
    assert state.COIN_DEVICE_ID is None


@pytest.mark.asyncio
async def test_nv4000_levels_not_connected(client: AsyncClient):
    """/device/nv4000/levels POST — 미연결 시 409."""
    resp = await client.post("/device/nv4000/levels", json=[{"value": 10000, "amount": 5}])
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_nv4000_get_levels_not_connected(client: AsyncClient):
    """/device/nv4000/levels GET — 미연결 시 409."""
    resp = await client.get("/device/nv4000/levels")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_nv4000_set_levels_success(client: AsyncClient):
    """/device/nv4000/levels POST — 연결 후 SetDenominationLevel 호출."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    mock_result = {"result": "OK"}
    with patch("app.services.itl_client.set_denomination_level", new=AsyncMock(return_value=mock_result)):
        resp = await client.post(
            "/device/nv4000/levels",
            json=[{"value": 20000, "amount": 5}, {"value": 10000, "amount": 3}],
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2
    assert all(r["ok"] for r in data["results"])


@pytest.mark.asyncio
async def test_both_connect_success(client: AsyncClient):
    """/device/connect POST — 두 장치 순차 연결 성공."""
    nv_response = {"deviceID": "NV4000-COM4", "isOpen": True, "error": None}
    coin_response = {"deviceID": "SMART_COIN_SYSTEM-COM3", "isOpen": True, "error": None}
    with (
        patch("app.services.itl_client.open_nv4000", new=AsyncMock(return_value=nv_response)),
        patch("app.services.itl_client.open_smart_coin", new=AsyncMock(return_value=coin_response)),
    ):
        resp = await client.post("/device/connect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nv4000"]["ok"] is True
    assert data["smart_coin"]["ok"] is True


# ── 동전 재고 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_coin_get_levels_not_connected(client: AsyncClient):
    """/device/coin/levels GET — 미연결 시 409."""
    resp = await client.get("/device/coin/levels")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_coin_get_levels_success(client: AsyncClient):
    """/device/coin/levels GET — 연결 후 재고 반환."""
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    mock_levels = [{"value": 100, "amount": 10}, {"value": 500, "amount": 5}]
    with patch("app.services.itl_client.get_all_levels", new=AsyncMock(return_value=mock_levels)):
        resp = await client.get("/device/coin/levels")
    assert resp.status_code == 200
    assert resp.json()["levels"] == mock_levels


# ── 수동 지급 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_coin_dispense_not_connected(client: AsyncClient):
    """/device/coin/dispense — 미연결 시 409."""
    resp = await client.post("/device/coin/dispense", json={"value": 3000})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_coin_dispense_success(client: AsyncClient):
    """/device/coin/dispense — 연결 후 지급 성공."""
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    mock_result = {"dispenseResult": "COMPLETED"}
    with (
        patch("app.services.itl_client.enable_payout", new=AsyncMock()),
        patch("app.services.itl_client.dispense_value", new=AsyncMock(return_value=mock_result)),
    ):
        resp = await client.post("/device/coin/dispense", json={"value": 3000})
    assert resp.status_code == 200
    assert resp.json()["result"]["dispenseResult"] == "COMPLETED"


@pytest.mark.asyncio
async def test_nv4000_dispense_not_connected(client: AsyncClient):
    """/device/nv4000/dispense — 미연결 시 409."""
    resp = await client.post("/device/nv4000/dispense", json={"value": 10000})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_nv4000_dispense_success(client: AsyncClient):
    """/device/nv4000/dispense — 연결 후 지급 성공."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    mock_result = {"dispenseResult": "COMPLETED"}
    with (
        patch("app.services.itl_client.enable_payout", new=AsyncMock()),
        patch("app.services.itl_client.dispense_value", new=AsyncMock(return_value=mock_result)),
    ):
        resp = await client.post("/device/nv4000/dispense", json={"value": 10000})
    assert resp.status_code == 200
    assert resp.json()["result"]["dispenseResult"] == "COMPLETED"


# ── 장치 모니터링 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_device_monitor_no_devices(client: AsyncClient):
    """/device/monitor — 미연결 시 connected=False."""
    resp = await client.get("/device/monitor")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nv4000"]["connected"] is False
    assert data["smart_coin"]["connected"] is False
    assert data["any_error"] is False


@pytest.mark.asyncio
async def test_device_monitor_idle(client: AsyncClient):
    """/device/monitor — IDLE 상태."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    mock_events = [{"type": "DeviceStatusResponse", "stateAsString": "IDLE"}]
    with patch("app.services.itl_client.get_device_status", new=AsyncMock(return_value=mock_events)):
        resp = await client.get("/device/monitor")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nv4000"]["state"] == "IDLE"
    assert data["nv4000"]["has_error"] is False


@pytest.mark.asyncio
async def test_device_monitor_jammed(client: AsyncClient):
    """/device/monitor — JAMMED 감지."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    mock_nv = [{"type": "DeviceStatusResponse", "stateAsString": "JAMMED", "eventTypeAsString": "JAMMED"}]
    mock_coin = [{"type": "DeviceStatusResponse", "stateAsString": "IDLE"}]
    with patch("app.services.itl_client.get_device_status", side_effect=[mock_nv, mock_coin]):
        resp = await client.get("/device/monitor")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nv4000"]["has_error"] is True
    assert "JAMMED" in data["nv4000"]["errors"]
    assert data["any_error"] is True
