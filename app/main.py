"""
NV4000(지폐) + SMART Coin(동전) 통합 결제 게이트웨이
실행: uvicorn app.main:app --port 8001
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import state
from app.services import itl_client
from app.routers import device, payment


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


app = FastAPI(title="NV4000 + SMART Coin 결제 게이트웨이", version="2.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(device.router)
app.include_router(payment.router)


# ── 헬스체크 ──────────────────────────────────────────────

@app.get("/health", tags=["시스템"])
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "nv4000_connected": state.NV_DEVICE_ID is not None,
        "coin_connected": state.COIN_DEVICE_ID is not None,
        "active_tx": state.ACTIVE_TX.get("tx_id") if state.ACTIVE_TX else None,
    }
