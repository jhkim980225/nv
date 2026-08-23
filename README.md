# NV4000 + SMART Coin 통합 결제 게이트웨이

NV4000 지폐 검증기(COM3)와 SMART Coin System 동전 투입기(COM4)를 동시에 제어하는 FastAPI 게이트웨이.
ITL REST API 서버(`http://localhost:5000`)를 중계하여 목표 금액 도달 시 거래를 완료하고 거스름돈을 지급한다.

## 요구사항

- Python 3.10+
- ITL REST API 서버 (Windows 서비스, 포트 5000)
- NV4000(COM3), SMART Coin System(COM4)

## 설치 및 실행

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

API 문서: http://localhost:8000/docs

## 결제 흐름

1. `POST /device/connect` — 두 장치 동시 연결
2. `POST /payment/start` — 수납 시작, 백그라운드 폴링(200ms)
3. 지폐 + 동전 누적 합계가 목표 금액 이상이면 완료
4. 잔액은 SMART Coin 우선으로 거스름돈 지급
5. `GET /payment/status/{tx_id}` — 거래 상태 조회

## 환경변수

`.env` 파일로 설정. 주요 항목:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ITL_BASE_URL` | `http://localhost:5000` | ITL REST API 서버 주소 |
| `NV4000_COM` / `NV4000_SSP` | `COM3` / `0` | 지폐 검증기 포트/주소 |
| `COIN_COM` / `COIN_SSP` | `COM4` / `16` | 동전 투입기 포트/주소 |
| `COIN_DENOMINATIONS` | `10,50,100,500` | 동전 권종 |
| `POLL_MS` | `200` | 폴링 주기 (ms) |

전체 목록과 아키텍처 상세는 [CLAUDE.md](CLAUDE.md) 참고.

## 파일 구조

| 파일 | 역할 |
|------|------|
| `main.py` | FastAPI 앱, 라우트 정의 |
| `config.py` | 환경변수 설정 |
| `state.py` | 전역 상태 (HTTP 클라이언트, deviceID, 활성 거래) |
| `itl_client.py` | ITL REST API 클라이언트 (인증 + 두 장치 공용) |
| `payment_service.py` | 결제 폴링 루프 및 거래 관리 |
| `schemas.py` | Pydantic 요청/응답 스키마 |
