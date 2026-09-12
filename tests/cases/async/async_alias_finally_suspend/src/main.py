# A movable local returned under a SUSPENDING finally that reads it through
# an alias: this pins that the pending-slot store does NOT move (a move
# would gut the object the finally's alias read still sees). The store is
# the pre-existing conservative COPY -- itself a tracked divergence for a
# MUTATING finally (BUGS.md: suspending-finally eager capture); the proper
# deferral fix must not regress this read-only shape.
import asyncio
from tpy import int32, Own


async def make() -> Own[list[int32]]:
    xs = [1, 2, 3]
    ys = xs
    try:
        return xs
    finally:
        await asyncio.sleep(0)
        print(len(ys))


async def driver() -> int32:
    r = await make()
    return len(r)


def main() -> None:
    print(asyncio.run(driver()))


main()
