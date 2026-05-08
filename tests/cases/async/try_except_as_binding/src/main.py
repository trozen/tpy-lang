# try/except around await with `as e` binding -- exercises the
# `catch (const T& binding)` path in _emit_resume_handlers (vs the
# bare-handler path covered by try_except_cancel).
import asyncio


async def worker() -> None:
    await asyncio.sleep(0.5)


async def main_coro() -> None:
    task = asyncio.create_task(worker())
    await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError as e:
        # Bind validates the catch (const T& binding) codegen path.
        # `e` is intentionally referenced (not just bound) so the
        # generated C++ doesn't optimize the binding away.
        _ = e
        print("caught")


def main() -> None:
    asyncio.run(main_coro())


main()
