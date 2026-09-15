# The same name collision inside an ASYNC body -- the coroutine twin of
# error_name_collision_in_generator. Both share the resumable frame, where a
# nested def is a named MEMBER of the frame struct rather than a lambda, so the
# collision is an unaudited hazard and keeps its own located reject even
# without the recursion or pre-def read the sync lambda form needs. Its own
# case because the compiler stops at the first error, so the generator case
# cannot also carry this position. The sync half of the same shadow rule is
# BUGS.md#nested-def-shadow-resolves-to-shadowed-callable, whose sema fix does
# NOT close this frame-member reject.
import asyncio

from tpy import int32


def tally(x: int32) -> int32:
    return x + 1


async def work() -> int32:  # tpyc: error(/res\.nested_def_member/)
    # async position: the frame-member nested def takes the module name
    def tally(x: int32) -> int32:
        return x + 100

    await asyncio.sleep(0)
    return tally(1)


def main() -> None:
    print(asyncio.run(work()))


main()
