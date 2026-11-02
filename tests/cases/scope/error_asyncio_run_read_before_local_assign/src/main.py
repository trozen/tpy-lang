# The asyncio.run argument check reads the argument as the call does: an
# async def's name the function later rebinds is the function's local there,
# so `asyncio.run(work)` is CPython's UnboundLocalError, not a missing call.
import asyncio
from tpy import int32


async def work() -> int32:
    return 1


def main() -> None:
    # The argument read the later `work = 5` makes local.
    print(asyncio.run(work))  # tpyc: error(/'work' is read before it is bound on line 15: .* does not see the module's 'work'/)
    work = 5
    print(work)


main()
