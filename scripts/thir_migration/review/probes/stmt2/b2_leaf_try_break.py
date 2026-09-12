import asyncio
from tpy import int32
async def f(n: int32) -> int32:
    t = 0
    for i in range(3):
        try:
            t += i
            if i > 1:
                break
        finally:
            t += 1
    await asyncio.sleep(0)
    return t
def main() -> None:
    pass
main()
