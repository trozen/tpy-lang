import asyncio
from tpy import int32
class Node:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
async def f(items: list[Node], n: int32) -> int32:
    a = items[0]
    await asyncio.sleep(0)
    a = Node(n)
    return a.x
def main() -> None:
    pass
main()
