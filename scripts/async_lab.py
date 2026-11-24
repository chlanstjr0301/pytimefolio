"""
asyncio 실습 4개. 네트워크 없이 돌아간다.
    python async_lab.py 1   # 순차 await vs 동시 실행
    python async_lab.py 2   # 블로킹 호출 하나가 루프 전체를 멈춘다
    python async_lab.py 3   # await를 사이에 둔 '확인 후 실행'은 asyncio에서도 경쟁 상태
    python async_lab.py 4   # 타임아웃과 취소: CancelledError는 await 지점에서 터진다
"""

import asyncio
import sys
import time

T0 = time.perf_counter()


def log(msg: str):
    print(f"{time.perf_counter() - T0:5.2f}s  {msg}")


async def fake_io(name: str, sec: float) -> str:
    """서버 응답을 기다리는 척. await 동안 이 코루틴은 멈추고 루프는 다른 일을 한다."""
    log(f"{name} 요청 보냄")
    await asyncio.sleep(sec)          # 여기서 제어권을 이벤트 루프에 돌려준다
    log(f"{name} 응답 받음")
    return name


# ---------------------------------------------------------------- 실습 1
async def lab1():
    log("== 순차: await를 차례로 ==")
    await fake_io("A", 1)
    await fake_io("B", 1)             # A가 끝나야 B를 시작한다 → 2초

    log("== 동시: Task 두 개를 루프에 올림 ==")
    async with asyncio.TaskGroup() as tg:   # Python 3.11+
        tg.create_task(fake_io("C", 1))
        tg.create_task(fake_io("D", 1))     # C를 기다리는 동안 D도 진행 → 1초
    log("끝")


# ---------------------------------------------------------------- 실습 2
async def heartbeat():
    """SignalR ping이라고 생각하자. 0.5초마다 찍혀야 정상."""
    for i in range(8):
        log(f"   ♥ ping {i}")
        await asyncio.sleep(0.5)


async def bad_worker():
    await asyncio.sleep(1)
    log("bad_worker: time.sleep(2) 시작 (블로킹)")
    time.sleep(2)                     # await가 아니다 → 루프가 2초 동안 아무것도 못 함
    log("bad_worker: 끝")


async def good_worker():
    await asyncio.sleep(1)
    log("good_worker: 무거운 계산을 스레드로 넘김")
    await asyncio.to_thread(time.sleep, 2)   # MVO 같은 계산은 이렇게
    log("good_worker: 끝")


async def lab2():
    log("== 블로킹 버전: ping 사이에 2초 공백이 생긴다 ==")
    async with asyncio.TaskGroup() as tg:
        tg.create_task(heartbeat())
        tg.create_task(bad_worker())
    log("== to_thread 버전: ping이 끊기지 않는다 ==")
    async with asyncio.TaskGroup() as tg:
        tg.create_task(heartbeat())
        tg.create_task(good_worker())


# ---------------------------------------------------------------- 실습 3
cash = 100


async def buy_unsafe(name: str, amt: int):
    global cash
    if cash >= amt:                   # ① 확인
        log(f"{name}: 잔고 {cash} ≥ {amt}, 주문 전송 중...")
        await asyncio.sleep(0.1)      # ② 서버 응답 대기 — 이 사이에 다른 Task가 끼어든다
        cash -= amt                   # ③ 실행
        log(f"{name}: 체결, 잔고 {cash}")


lock = asyncio.Lock()


async def buy_safe(name: str, amt: int):
    global cash
    async with lock:                  # 확인~실행 구간을 한 Task만 지나가게
        if cash >= amt:
            log(f"{name}: 잔고 {cash} ≥ {amt}, 주문 전송 중...")
            await asyncio.sleep(0.1)
            cash -= amt
            log(f"{name}: 체결, 잔고 {cash}")
        else:
            log(f"{name}: 잔고 {cash} < {amt}, 거절")


async def lab3():
    global cash
    log("== 락 없음: 70짜리 주문 둘이 모두 통과 ==")
    cash = 100
    await asyncio.gather(buy_unsafe("X", 70), buy_unsafe("Y", 70))
    log(f"최종 잔고 {cash}  ← 음수")
    log("== 락 있음 ==")
    cash = 100
    await asyncio.gather(buy_safe("X", 70), buy_safe("Y", 70))
    log(f"최종 잔고 {cash}")


# ---------------------------------------------------------------- 실습 4
async def slow_server():
    try:
        log("slow_server: 응답 대기 시작")
        await asyncio.sleep(5)
        log("slow_server: (여기는 오지 않는다)")
    except asyncio.CancelledError:
        log("slow_server: CancelledError가 await 지점에서 발생")
        raise                         # 삼키지 말고 다시 던지는 게 규칙
    finally:
        log("slow_server: finally — 연결 정리 같은 뒷정리는 여기서")


async def lab4():
    try:
        await asyncio.wait_for(slow_server(), timeout=1)
    except TimeoutError:
        log("호출자: TimeoutError 수신 → 재시도 여부 판단")


if __name__ == "__main__":
    labs = {"1": lab1, "2": lab2, "3": lab3, "4": lab4}
    asyncio.run(labs[sys.argv[1] if len(sys.argv) > 1 else "1"]())
