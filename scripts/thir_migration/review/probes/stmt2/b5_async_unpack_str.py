import asyncio
from tpy import Int32, Own
def mk(n: Int32) -> tuple[Own[str], Int32]:
    return ('a', n)
async def f(n: Int32) -> Int32:
    s, k = mk(n)
    await asyncio.sleep(0)
    return k + len(s)
def main() -> None:
    pass
main()
