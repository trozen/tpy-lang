# A @nocopy receiver returned via `async def -> C: return self`: the
# borrow ABI must not materialize any copy, so this compiles only if the
# await binding and the Poll payload alias throughout.
import asyncio
from tpy import nocopy


@nocopy
class Res:
    n: int

    def __init__(self) -> None:
        self.n = 3

    async def me(self) -> "Res":
        await asyncio.sleep(0)
        return self


async def main() -> None:
    res = Res()
    r = await res.me()
    r.n = 8
    print(res.n)


asyncio.run(main())
