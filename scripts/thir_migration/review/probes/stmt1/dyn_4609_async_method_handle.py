from tpy import int32
import asyncio
class C:
    n: int32
    def __init__(self) -> None:
        self.n = 3
    async def step(self) -> int32:
        return self.n
def main() -> None:
    c = C()
    h = c.step()
    print(asyncio.run(h))
main()
