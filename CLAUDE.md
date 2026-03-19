# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

NV4000(지폐 검증기, COM3)과 SMART Coin System(동전 투입기, COM4)을 **동시에 연결**하여, 지정 금액에 도달하면 거래를 완료하는 통합 결제 게이트웨이.

- **기술 스택**: Python FastAPI + httpx (비동기)
- **장치 통신**: ITL REST API 서버(`http://localhost:5000`)를 중계하여 두 장치 동시 제어
- **언어/주석**: 한국어

## 아키텍처

```
FastAPI 게이트웨이 (포트 8000)
        │ HTTP (httpx)
        ▼
ITL REST API 서버 (포트 5000)  ← ITL 제공 Windows 서비스
        │                │
        │ Serial COM3    │ Serial COM4
        ▼                ▼
  NV4000 지폐          SMART Coin
  검증기               동전 투입기
```

- ITL 서버는 여러 장치를 동시 관리. 각 장치는 `OpenConnection` 후 고유 `deviceID` 부여.
  - NV4000 → `NV4000-COM3`
  - SMART Coin → `SMART_COIN_SYSTEM-COM4`
- 모든 API 호출은 `?deviceID=...` 쿼리 파라미터 + `Authorization: Bearer <token>` 헤더 필요.
- 토큰 만료(7일) 또는 401 응답 시 자동 재인증.

## 파일 구조

```
nvsmart/
├── main.py            # FastAPI 앱 진입점, 모든 라우트 정의
├── config.py          # 환경변수 설정 (ITL 서버, COM 포트, 폴링 주기)
├── state.py           # 전역 상태 (HTTP 클라이언트, deviceID, 활성 거래)
├── itl_client.py      # ITL REST API 클라이언트 (인증 + 두 장치 공용)
├── payment_service.py # 결제 폴링 루프 및 거래 관리
├── schemas.py         # Pydantic 요청/응답 스키마
├── .env               # 환경변수 파일
└── requirements.txt
```

## 개발 명령어

```bash
# 의존성 설치
pip install -r requirements.txt

# 서버 실행 (개발)
uvicorn main:app --reload --port 8000

# API 문서
# http://localhost:8000/docs
```

## ITL REST API 핵심 엔드포인트

모든 요청은 `http://localhost:5000` 기준. 인증 후 모든 요청에 `Authorization: Bearer <token>` 필요.

### 인증
```
POST /api/Users/Authenticate
  Body: {"Username": "admin", "Password": "password"}
  Response: {"token": "...", "token_type": "Bearer"}
```

### 장치 연결 (OpenConnection)
```
POST /api/CashDevice/OpenConnection
  NV4000 Body:
    {"ComPort": "COM3", "SspAddress": 0,
     "EnableAcceptor": true, "EnableAutoAcceptEscrow": true, "EnablePayout": true}

  SMART Coin Body:
    {"ComPort": "COM4", "SspAddress": 16,
     "Currency": "KRW", "Denominations": [10,50,100,500],
     "EnableAcceptor": true, "EnablePayout": true}

  Response: {"deviceID": "NV4000-COM3", ...}
```

### 수락기 제어
```
POST /api/CashDevice/EnableAcceptor?deviceID=...      # 지폐/동전 수납 시작
POST /api/CashDevice/DisableAcceptor?deviceID=...     # 수납 중지
POST /api/CashDevice/EnableCoinMechOrFeeder?deviceID=...  # SMART Coin 전용, Body: true/false
```

### 상태 폴링 (200ms 권장)
```
GET /api/CashDevice/GetDeviceStatus?deviceID=...
  Response: [
    {"type": "DeviceStatusResponse", "stateAsString": "ACCEPTING"},
    {"type": "CashEventResponse", "eventTypeAsString": "STORED", "value": 1000, "countryCode": "KRW"}
  ]
```

**누적 대상 이벤트**: `STORED`, `STACKED`, `VALUE_ADDED` (ESCROW는 대기 상태로 누적 안 함)

**DeviceStatus 상태값**: `IDLE`, `ACCEPTING`, `DISPENSING`, `JAMMED`, `ERROR` 등

### 거스름돈 지급
```
POST /api/CashDevice/EnablePayout?deviceID=...        # 지급 전 활성화 필수
POST /api/CashDevice/DispenseValue?deviceID=...
  Body: {"Value": 500, "CountryCode": "KRW"}
  Response: {"dispenseResult": "COMPLETED"|"ERROR", "payoutOperationData": "..."}
```

### 기타
```
POST /api/CashDevice/DisconnectDevice?deviceID=...    # 연결 해제
GET  /api/CashDevice/GetAllLevels?deviceID=...        # 권종별 재고 조회
```

## 결제 흐름

```
1. POST /device/connect    → NV4000(COM3) + SMART Coin(COM4) 동시 OpenConnection
2. POST /payment/start     → EnableAcceptor(NV4000) + EnableAcceptor+CoinMech(SMART Coin)
                             백그라운드 폴링 태스크 시작
3. 폴링 루프 (200ms)       → 두 장치 동시 GetDeviceStatus
                             bill_amount + coin_amount 누적
                             합계 >= target_amount 시 COMPLETE
4. 거래 완료               → DisableAcceptor (두 장치)
                             change = total - target → DispenseValue (SMART Coin 우선)
5. GET /payment/status/{tx_id}  → 상태, 수납금액, 거스름돈 결과 조회
```

## 환경변수 (.env)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ITL_BASE_URL` | `http://localhost:5000` | ITL REST API 서버 주소 |
| `ITL_USERNAME` | `admin` | 인증 아이디 |
| `ITL_PASSWORD` | `password` | 인증 비밀번호 |
| `NV4000_COM` | `COM3` | 지폐 검증기 COM 포트 |
| `NV4000_SSP` | `0` | 지폐 검증기 SSP 주소 |
| `NV4000_CURRENCY` | `KRW` | 지폐 통화 코드 |
| `COIN_COM` | `COM4` | 동전 투입기 COM 포트 |
| `COIN_SSP` | `16` | 동전 투입기 SSP 주소 |
| `COIN_CURRENCY` | `KRW` | 동전 통화 코드 |
| `COIN_DENOMINATIONS` | `10,50,100,500` | 동전 권종 (원 단위) |
| `POLL_MS` | `200` | 폴링 주기 (밀리초) |

## 참고 프로젝트

- `C:\Feda\SmartCoin` — SMART Coin 단독 FastAPI 래퍼 (itl_client.py, main.py 참고)
- `C:\dev\coconut` — NV4000 FastAPI 게이트웨이 (payment_service.py 폴링 패턴 참고)

## 주요 패턴

- **동시성**: `asyncio.Lock(STATE_LOCK)`으로 상태 변경 보호, `asyncio.gather()`로 두 장치 동시 폴링
- **인증**: 지연 초기화(첫 요청 시 인증), 401 응답 시 자동 재인증 후 1회 재시도
- **거스름돈**: SMART Coin 우선 지급, 실패 시 NV4000 시도
- **거래 상태**: 메모리(`state.ACTIVE_TX`)에만 저장 (DB 없음)
