"""테스트 공용 픽스처"""
import pytest
import httpx
from httpx import AsyncClient, ASGITransport

from app.models import state


@pytest.fixture(autouse=True)
async def reset_state():
    """각 테스트 전후 전역 상태 초기화."""
    state.HTTP = httpx.AsyncClient(timeout=5.0)
    state.AUTH_HEADER = None
    state.NV_DEVICE_ID = None
    state.COIN_DEVICE_ID = None
    state.ACTIVE_TX = None
    state.ACTIVE_TX_TASK = None
    state.TX_HISTORY = []
    state.ALERTS = []
    yield
    await state.HTTP.aclose()
    state.HTTP = None


@pytest.fixture
async def client():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
