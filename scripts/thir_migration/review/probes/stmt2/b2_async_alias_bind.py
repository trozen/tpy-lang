import asyncio
from tpy import Int32
class Node:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
async def f(items: list[Node], n: Int32) -> Int32:
    a = items[0]
    await asyncio.sleep(0)
    a = Node(n)
    return a.x
def main() -> None:
    pass
main()
