# Regression: in an async coroutine (the resumable frame shared with
# generators), a finally / __exit__ that RAISES around an await must run
# exactly once and propagate the first exception -- the async shape of
# gen_finally_raises_once.
import asyncio

_code = 0


def bump() -> int:
    global _code
    _code += 1
    print(f"side-effect {_code}")
    return _code


class Err(Exception):
    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


class Thrower:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        raise Err(bump())


async def normal_exit() -> None:
    try:
        await asyncio.sleep(0)
    finally:
        raise Err(bump())


async def return_exit() -> None:
    try:
        await asyncio.sleep(0)
        return
    finally:
        raise Err(bump())


async def with_exit() -> None:
    with Thrower():
        await asyncio.sleep(0)


async def handler_exit() -> None:
    # finally raises after the except handler completes normally.
    try:
        await asyncio.sleep(0)
        raise ValueError("v")
    except ValueError:
        print("caught")
    finally:
        raise Err(bump())


async def nested_exit() -> None:
    # inner finally raises on the return path; outer finally must still run.
    try:
        try:
            await asyncio.sleep(0)
            return
        finally:
            print("inner fin")
            raise Err(bump())
    finally:
        print("outer fin")


async def main() -> None:
    global _code
    _code = 0
    print("-- normal_exit --")
    try:
        await normal_exit()
    except Err as e:
        print(f"caught code={e.code}")

    _code = 0
    print("-- return_exit --")
    try:
        await return_exit()
    except Err as e:
        print(f"caught code={e.code}")

    _code = 0
    print("-- with_exit --")
    try:
        await with_exit()
    except Err as e:
        print(f"caught code={e.code}")

    _code = 0
    print("-- handler_exit --")
    try:
        await handler_exit()
    except Err as e:
        print(f"caught code={e.code}")

    _code = 0
    print("-- nested_exit --")
    try:
        await nested_exit()
    except Err as e:
        print(f"caught code={e.code}")


asyncio.run(main())
