import asyncio
from tpy import int32, Own
def mk(n: int32) -> tuple[Own[str], int32]:
    return ('a', n)
async def f(n: int32) -> int32:
    s, k = mk(n)
    await asyncio.sleep(0)
    return k + len(s)
def main() -> None:
    pass
main()
