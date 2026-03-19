"""환경변수 설정 모듈"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── ITL REST API 서버 ─────────────────────────────────────
ITL_BASE_URL: str = os.getenv("ITL_BASE_URL", "http://localhost:5000").rstrip("/")
ITL_USERNAME: str = os.getenv("ITL_USERNAME", "admin")
ITL_PASSWORD: str = os.getenv("ITL_PASSWORD", "password")

# ── NV4000 지폐 검증기 ────────────────────────────────────
NV4000_COM: str = os.getenv("NV4000_COM", "COM4")
NV4000_SSP: int = int(os.getenv("NV4000_SSP", "0"))
NV4000_CURRENCY: str = os.getenv("NV4000_CURRENCY", "MXN")

# ── SMART Coin 동전 투입기 ────────────────────────────────
COIN_COM: str = os.getenv("COIN_COM", "COM4")
COIN_SSP: int = int(os.getenv("COIN_SSP", "16"))
COIN_CURRENCY: str = os.getenv("COIN_CURRENCY", "MXN")
COIN_DENOMINATIONS: list[int] = [
    int(x.strip()) for x in os.getenv("COIN_DENOMINATIONS", "50,100,200,500,1000").split(",")
]

# ── 폴링 설정 ─────────────────────────────────────────────
POLL_MS: int = int(os.getenv("POLL_MS", "200"))
POLL_SEC: float = max(0.05, POLL_MS / 1000.0)  # 최소 50ms 보장
