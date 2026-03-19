"""결제 엔드포인트 테스트"""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient

from app.models import state


@pytest.mark.asyncio
async def test_payment_start_no_device(client: AsyncClient):
    """/payment/start — 장치 미연결 시 409."""
    resp = await client.post("/payment/start", json={"target_amount": 10000, "timeout_sec": 60})
    assert resp.status_code == 409
    assert "연결" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_payment_start_duplicate(client: AsyncClient):
    """/payment/start — 이미 ACTIVE 거래 시 409."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    state.ACTIVE_TX = {"status": "ACTIVE", "tx_id": "existing"}
    resp = await client.post("/payment/start", json={"target_amount": 10000, "timeout_sec": 60})
    assert resp.status_code == 409
    assert "진행 중" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_payment_current_empty(client: AsyncClient):
    """/payment/current — 거래 없을 때 active=False."""
    resp = await client.get("/payment/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is False
    assert data["tx"] is None


@pytest.mark.asyncio
async def test_payment_current_active(client: AsyncClient):
    """/payment/current — ACTIVE 거래 정보 반환."""
    import time
    state.ACTIVE_TX = {
        "tx_id": "abc123",
        "status": "ACTIVE",
        "target_amount": 10000,
        "bill_amount": 5000,
        "coin_amount": 0,
        "started_at": time.time(),
    }
    resp = await client.get("/payment/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is True
    assert data["tx"]["tx_id"] == "abc123"
    assert data["tx"]["remaining_amount"] == 5000


@pytest.mark.asyncio
async def test_payment_status_not_found(client: AsyncClient):
    """/payment/status/{tx_id} — 없는 거래 404."""
    resp = await client.get("/payment/status/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_payment_status_found(client: AsyncClient):
    """/payment/status/{tx_id} — 정상 상태 반환."""
    import time
    state.ACTIVE_TX = {
        "tx_id": "tx001",
        "status": "COMPLETE",
        "target_amount": 10000,
        "bill_amount": 10000,
        "coin_amount": 0,
        "change_amount": 0,
        "change_result": None,
        "events": [],
        "started_at": time.time(),
        "error": None,
    }
    resp = await client.get("/payment/status/tx001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "COMPLETE"
    assert data["total_amount"] == 10000


@pytest.mark.asyncio
async def test_payment_cancel_not_found(client: AsyncClient):
    """/payment/cancel/{tx_id} — 없는 거래 취소 시 404."""
    resp = await client.post("/payment/cancel/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_payment_cancel_success(client: AsyncClient):
    """/payment/cancel/{tx_id} — ACTIVE 거래 취소 성공."""
    import time
    state.ACTIVE_TX = {
        "tx_id": "tx_cancel",
        "status": "ACTIVE",
        "target_amount": 10000,
        "bill_amount": 0,
        "coin_amount": 0,
        "change_amount": 0,
        "events": [],
        "started_at": time.time(),
        "error": None,
    }
    with patch("app.services.payment_service._disable_all", new=AsyncMock()):
        resp = await client.post("/payment/cancel/tx_cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_payment_start_success(client: AsyncClient):
    """/payment/start — 장치 연결 후 거래 시작."""
    state.NV_DEVICE_ID = "NV4000-COM4"
    state.COIN_DEVICE_ID = "SMART_COIN_SYSTEM-COM3"
    with (
        patch("app.services.itl_client.enable_acceptor", new=AsyncMock()),
        patch("app.services.itl_client.enable_coin_mech", new=AsyncMock()),
        patch("app.services.payment_service._poll_loop", new=AsyncMock()),
    ):
        resp = await client.post("/payment/start", json={"target_amount": 10000, "timeout_sec": 60})
    assert resp.status_code == 200
    data = resp.json()
    assert "tx_id" in data
    assert data["target_amount"] == 10000
