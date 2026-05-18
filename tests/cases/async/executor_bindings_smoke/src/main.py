# asyncio internal-helpers smoke: current-executor getter/setter/clear,
# time.sleep_until_steady. Smoke-checks that the helpers parse, compile,
# link, and behave, plus that asyncio.run's _ExecutorScope correctly
# clears `_current_executor` on teardown so a stale post-run wake is a
# silent no-op rather than a stale-pointer dispatch.
import asyncio
from asyncio._executor import (
    _clear_current_executor,
    _get_current_executor,
    _set_current_executor,
)
from time import monotonic, sleep_until_steady


def check_outside() -> None:
    print("outside null?", (_get_current_executor() is None))


async def check_inside() -> None:
    h = _get_current_executor()
    print("inside null?", (h is None))
    # Manual save/restore round-trip via the public bindings; not
    # the production path (that's _ExecutorScope.__del__, exercised
    # by check_teardown below).
    _clear_current_executor()
    print("after clear:", (_get_current_executor() is None))
    _set_current_executor(h)
    print("after restore:", (_get_current_executor() is None))


def check_sleep() -> None:
    sleep_until_steady(monotonic() - 1.0)
    print("sleep_until_steady past deadline: ok")


async def trivial() -> None:
    return None


def check_teardown() -> None:
    # `_current_executor` should be null after asyncio.run returns --
    # `_ExecutorScope.__del__` has cleared it. Direct evidence that the
    # RAII teardown ran.
    asyncio.run(trivial())
    print("after run, null?", (_get_current_executor() is None))
    # A second asyncio.run is allowed because `_current_executor` was
    # cleared (re-entry into a running event loop is what gets
    # rejected; back-to-back runs are fine).
    asyncio.run(trivial())
    print("second run completed, null?", (_get_current_executor() is None))


def main() -> None:
    check_outside()
    check_sleep()
    asyncio.run(check_inside())
    check_teardown()


main()
