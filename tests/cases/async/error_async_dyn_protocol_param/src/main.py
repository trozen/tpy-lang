# The async half of the @dynamic-protocol frame param stays rejected: the call-
# site RefAdapter temp the frame would borrow does not outlive the first
# suspension. Pins a clean FRONT-END reject, never a C++ build error.
# BUGS.md#res-param-dyn-protocol-frame
import asyncio
from tpy import int32, dynamic
from typing import Protocol


@dynamic
class Src(Protocol):
    def get(self) -> int32: ...

    def bump(self) -> None: ...


class Impl:
    n: int32

    def __init__(self) -> None:
        self.n = 7

    def get(self) -> int32:
        return self.n

    def bump(self) -> None:
        self.n += 1


# The generator sibling of this signature compiles; the async one does not.
async def body(s: Src) -> None:  # tpyc: error(/not yet supported/)
    await asyncio.sleep(0)
    s.bump()
    print("body", s.get())


async def driver() -> None:
    im = Impl()
    await body(im)
    print("after", im.get())


def main() -> None:
    asyncio.run(driver())


main()
