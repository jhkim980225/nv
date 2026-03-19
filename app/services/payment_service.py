"""
결제 서비스 — NV4000(지폐) + SMART Coin(동전) 동시 수납

거래 상태:
  ACTIVE    - 수납 진행 중
  COMPLETE  - 목표 금액 달성
  CANCELLED - 사용자 취소
  TIMEOUT   - 타임아웃 초과
  ERROR     - 시스템 오류
"""
import asyncio
import time
import uuid

from app import config
from app.services import itl_client
from app.models import state

_ACCUMULATE_EVENTS = {"STORED", "STACKED", "VALUE_ADDED"}


def _extract_cash_events(events: list[dict]) -> list[dict]:
    return [
        ev for ev in events
        if ev.get("type") == "CashEventResponse"
        and ev.get("eventTypeAsString") in _ACCUMULATE_EVENTS
    ]


async def start_payment(target_amount: int, timeout_sec: int) -> str:
    """결제 거래 시작. 두 장치 수락기 활성화 후 폴링 시작."""
    async with state.STATE_LOCK:
        if state.ACTIVE_TX and state.ACTIVE_TX.get("status") == "ACTIVE":
            raise ValueError("이미 진행 중인 거래가 있습니다")
        if not state.NV_DEVICE_ID or not state.COIN_DEVICE_ID:
            raise ValueError("두 장치가 모두 연결되어야 합니다. NV4000과 SMART Coin을 먼저 연결하세요")

        tx_id = uuid.uuid4().hex
        state.ACTIVE_TX = {
            "tx_id": tx_id,
            "status": "ACTIVE",
            "target_amount": target_amount,
            "bill_amount": 0,
            "coin_amount": 0,
            "change_amount": 0,
            "change_result": None,
            "events": [],
            "started_at": time.time(),
            "timeout_sec": timeout_sec,
            "error": None,
        }

    try:
        await asyncio.gather(
            itl_client.enable_acceptor(state.NV_DEVICE_ID),
            _enable_coin(),
        )
    except Exception as e:
        async with state.STATE_LOCK:
            state.ACTIVE_TX["status"] = "ERROR"
            state.ACTIVE_TX["error"] = f"수락기 활성화 실패: {e}"
        raise

    state.ACTIVE_TX_TASK = asyncio.create_task(_poll_loop(tx_id))
    return tx_id


async def _enable_coin() -> None:
    await itl_client.enable_acceptor(state.COIN_DEVICE_ID)
    await itl_client.enable_coin_mech(state.COIN_DEVICE_ID)


async def _disable_all() -> None:
    """두 장치 수락기 비활성화 (오류 무시)."""
    tasks = []
    if state.NV_DEVICE_ID:
        tasks.append(itl_client.disable_acceptor(state.NV_DEVICE_ID))
    if state.COIN_DEVICE_ID:
        tasks.append(itl_client.disable_acceptor(state.COIN_DEVICE_ID))
        tasks.append(itl_client.disable_coin_mech(state.COIN_DEVICE_ID))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _poll_loop(tx_id: str) -> None:
    """두 장치 동시 폴링 — 200ms 간격, 합산 금액 >= 목표 시 완료."""
    try:
        # 잔류 이벤트 플러시
        await asyncio.gather(
            itl_client.get_device_status(state.NV_DEVICE_ID),
            itl_client.get_device_status(state.COIN_DEVICE_ID),
            return_exceptions=True,
        )

        while True:
            await asyncio.sleep(config.POLL_SEC)

            tx = state.ACTIVE_TX
            if not tx or tx.get("tx_id") != tx_id or tx.get("status") != "ACTIVE":
                break

            if time.time() - tx["started_at"] >= tx["timeout_sec"]:
                async with state.STATE_LOCK:
                    tx["status"] = "TIMEOUT"
                    tx["error"] = f"타임아웃 ({tx['timeout_sec']}초 초과)"
                break

            nv_result, coin_result = await asyncio.gather(
                itl_client.get_device_status(state.NV_DEVICE_ID),
                itl_client.get_device_status(state.COIN_DEVICE_ID),
                return_exceptions=True,
            )

            async with state.STATE_LOCK:
                tx = state.ACTIVE_TX
                if not tx or tx.get("tx_id") != tx_id or tx.get("status") != "ACTIVE":
                    break

                if isinstance(nv_result, list):
                    for ev in _extract_cash_events(nv_result):
                        value = int(ev.get("value", 0))
                        tx["bill_amount"] += value
                        tx["events"].append({"device": "NV4000", "type": ev.get("eventTypeAsString"), "amount": value})
                elif isinstance(nv_result, Exception):
                    tx["events"].append({"device": "NV4000", "type": "POLL_ERROR", "error": str(nv_result)})

                if isinstance(coin_result, list):
                    for ev in _extract_cash_events(coin_result):
                        value = int(ev.get("value", 0))
                        tx["coin_amount"] += value
                        tx["events"].append({"device": "SMART_COIN", "type": ev.get("eventTypeAsString"), "amount": value})
                elif isinstance(coin_result, Exception):
                    tx["events"].append({"device": "SMART_COIN", "type": "POLL_ERROR", "error": str(coin_result)})

                total = tx["bill_amount"] + tx["coin_amount"]
                if total >= tx["target_amount"]:
                    tx["status"] = "COMPLETE"
                    tx["change_amount"] = total - tx["target_amount"]
                    break

    except asyncio.CancelledError:
        async with state.STATE_LOCK:
            tx = state.ACTIVE_TX
            if tx and tx.get("tx_id") == tx_id and tx.get("status") == "ACTIVE":
                tx["status"] = "CANCELLED"
        raise

    except Exception as e:
        async with state.STATE_LOCK:
            tx = state.ACTIVE_TX
            if tx and tx.get("tx_id") == tx_id:
                tx["status"] = "ERROR"
                tx["error"] = f"폴링 오류: {e}"

    finally:
        await _disable_all()

        tx = state.ACTIVE_TX
        if tx and tx.get("tx_id") == tx_id and tx.get("status") == "COMPLETE":
            change = tx.get("change_amount", 0)
            if change > 0:
                result = await _dispense_change(change)
                async with state.STATE_LOCK:
                    if state.ACTIVE_TX and state.ACTIVE_TX.get("tx_id") == tx_id:
                        state.ACTIVE_TX["change_result"] = result


async def _dispense_change(change: int) -> dict:
    """SMART Coin 우선 거스름돈 지급, 실패 시 NV4000 시도."""
    try:
        # DisableCoinMech 직후 EnablePayout이 일시 실패할 수 있으므로 재시도
        for attempt in range(3):
            await asyncio.sleep(1)
            try:
                await itl_client.enable_payout(state.COIN_DEVICE_ID)
                break
            except Exception:
                if attempt == 2:
                    pass  # 3번 모두 실패해도 DispenseValue 시도
        result = await itl_client.dispense_value(state.COIN_DEVICE_ID, change, config.COIN_CURRENCY)
        if result.get("dispenseResult") == "COMPLETED":
            return {"result": "OK", "amount": change, "device": "SMART_COIN", "detail": result}
        coin_error = result
    except Exception as e:
        coin_error = {"error": str(e)}

    try:
        await itl_client.enable_payout(state.NV_DEVICE_ID)
        result2 = await itl_client.dispense_value(state.NV_DEVICE_ID, change, config.NV4000_CURRENCY)
        return {
            "result": "OK" if result2.get("dispenseResult") == "COMPLETED" else "ERROR",
            "amount": change, "device": "NV4000", "detail": result2, "coin_error": coin_error,
        }
    except Exception as e:
        return {"result": "ERROR", "amount": change, "error": f"SMART_COIN: {coin_error}, NV4000: {e}"}


async def cancel_payment(tx_id: str) -> bool:
    async with state.STATE_LOCK:
        tx = state.ACTIVE_TX
        if not tx or tx.get("tx_id") != tx_id or tx.get("status") != "ACTIVE":
            return False
        tx["status"] = "CANCELLED"

    task = state.ACTIVE_TX_TASK
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return True
