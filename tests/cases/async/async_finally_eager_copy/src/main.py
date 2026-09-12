# A movable (BigInt) local returned under a NON-suspending finally: the
# eager pre-finally capture must not move (the snapshot pins the bare
# render `= total;`) -- only the direct-ready site moves. The annotation
# makes total a genuine BigInt; an inferred int32 would render through a
# BigInt(...) coercion, which the bare-name gate excludes at every site.
import asyncio
from tpy import int32


async def total_up(k: int32) -> int:
    total: int = 1
    for i in range(k):
        total *= i + 2
    try:
        return total
    finally:
        print("done")


def main() -> None:
    print(asyncio.run(total_up(3)))


main()
