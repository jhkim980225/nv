"""Pydantic 요청/응답 스키마"""
from typing import Any, Optional
from pydantic import BaseModel, Field


class PaymentStartRequest(BaseModel):
    """결제 시작 요청"""
    target_amount: int = Field(..., ge=1, description="수납 목표 금액 (센타보)")
    timeout_sec: int = Field(120, ge=10, le=600, description="타임아웃 초. 기본 120초")


class PaymentStatusResponse(BaseModel):
    """결제 상태 응답"""
    tx_id: str
    status: str  # ACTIVE | COMPLETE | CANCELLED | TIMEOUT | ERROR
    target_amount: int = Field(description="목표 금액 (센타보)")
    bill_amount: int = Field(description="지폐로 수납된 금액 (센타보)")
    coin_amount: int = Field(description="동전으로 수납된 금액 (센타보)")
    total_amount: int = Field(description="총 수납 금액 (센타보)")
    remaining_amount: int = Field(description="남은 금액 (센타보). 0이면 목표 달성")
    change_amount: int = Field(description="거스름돈 금액 (센타보)")
    change_result: Optional[dict[str, Any]] = Field(None, description="거스름돈 지급 결과")
    events: list[dict[str, Any]] = Field(description="최근 수납 이벤트 목록 (최대 50개)")
    elapsed_sec: float = Field(description="거래 경과 시간 (초)")
    error: Optional[str] = Field(None, description="오류 메시지")


class LevelItem(BaseModel):
    """리사이클러 권종별 재고 설정"""
    value: int   # 권종 (센타보 단위, 예: 20000 = 200페소)
    amount: int  # 장수
