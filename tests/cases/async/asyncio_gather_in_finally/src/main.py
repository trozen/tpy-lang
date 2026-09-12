# asyncio.gather_list invoked from inside a `finally` clause -- exercises
# the M3.3 CFG-based await-in-finally lowering, with gather_list as the
# sub-coro at the finally suspension point. The inner try raises so
# `__finally_exc_0` is populated (a pending exception is in flight) when
# gather_list suspends; this pins that the resume cancel-check on the
# gather_list sub-future doesn't trip over the finally's pending-
# exception slot. After the finally completes (gather drains its sub-
# tasks), the inner RuntimeError re-propagates and is caught by the
# outer handler.
import asyncio
from tpy import int32


async def cleanup_task(label: str) -> int32:
    await asyncio.sleep(0.001)
    print("cleanup", label)
    return int32(0)


async def main_coro() -> None:
    try:
        try:
            print("inner-try")
            raise RuntimeError("inner-fail")
        finally:
            tasks: list[asyncio.Task[int32]] = []
            tasks.append(asyncio.create_task(cleanup_task("a")))
            tasks.append(asyncio.create_task(cleanup_task("b")))
            await asyncio.gather_list(tasks)
            print("finally-done")
    except RuntimeError as e:
        print("caught:", e)


def main() -> None:
    asyncio.run(main_coro())


main()
