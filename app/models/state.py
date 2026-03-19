"""전역 상태 관리"""
import asyncio
from typing import Any, Optional
import httpx

HTTP: Optional[httpx.AsyncClient] = None
AUTH_HEADER: Optional[str] = None
NV_DEVICE_ID: Optional[str] = None    # 예: "NV4000-COM4"
COIN_DEVICE_ID: Optional[str] = None  # 예: "SMART_COIN_SYSTEM-COM4"
LAST_ITL_OK_AT: float = 0.0
ACTIVE_TX: Optional[dict[str, Any]] = None
ACTIVE_TX_TASK: Optional[asyncio.Task] = None
STATE_LOCK = asyncio.Lock()
TX_HISTORY: list[dict[str, Any]] = []
TX_HISTORY_MAX: int = 20
ALERTS: list[dict[str, Any]] = []
ALERTS_MAX: int = 100
