from tpy import Int32
import asyncio
class C:
    n: Int32
    def __init__(self) -> None:
        self.n = 3
    async def step(self) -> Int32:
        return self.n
def main() -> None:
    c = C()
    h = c.step()
    print(asyncio.run(h))
main()
