# tpy: ext_module
# CPython extension whose @export functions run `asyncio.run` inside the host
# interpreter. With no process-level signal layer the run arms one for its own
# duration, installs its SIGINT handler over it, and must hand CPython its own
# SIGINT handling back when it ends.
import asyncio
import signal

from tpy import int64
from tpy.extern import export


async def double(n: int64) -> int64:
    await asyncio.sleep(0.0)
    return n * 2


async def cancelled_by_sigint() -> int64:
    try:
        # The run's own SIGINT handler cancels the root task.
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(10.0)
        return 1
    except asyncio.CancelledError:
        return 42


@export
def run_double(n: int64) -> int64:
    return asyncio.run(double(n))


@export
def run_interrupted() -> int64:
    return asyncio.run(cancelled_by_sigint())
