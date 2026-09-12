# try/except/finally with awaits in both try and except bodies.
# Finally runs after both normal exit and after the except handler.
import asyncio
from tpy import int32

async def value(n: int32) -> int32:
    return n

async def fail() -> int32:
    raise ValueError("oops")

async def go(should_fail: bool) -> int32:
    result = int32(0)
    try:
        a = await value(int32(1))
        result = a
        if should_fail:
            b = await fail()
            result = result + b
        else:
            b = await value(int32(2))
            result = result + b
    except ValueError:
        result = int32(99)
    finally:
        print("cleanup")
    return result

def main() -> None:
    print(asyncio.run(go(False)))
    print(asyncio.run(go(True)))

main()
