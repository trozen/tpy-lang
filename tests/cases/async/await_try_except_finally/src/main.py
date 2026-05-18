# try/except/finally with awaits in both try and except bodies.
# Finally runs after both normal exit and after the except handler.
import asyncio
from tpy import Int32

async def value(n: Int32) -> Int32:
    return n

async def fail() -> Int32:
    raise ValueError("oops")

async def go(should_fail: bool) -> Int32:
    result = Int32(0)
    try:
        a = await value(Int32(1))
        result = a
        if should_fail:
            b = await fail()
            result = result + b
        else:
            b = await value(Int32(2))
            result = result + b
    except ValueError:
        result = Int32(99)
    finally:
        print("cleanup")
    return result

def main() -> None:
    print(asyncio.run(go(False)))
    print(asyncio.run(go(True)))

main()
