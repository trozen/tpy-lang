# Nested try/except/finally with awaits at each level.
# Verifies finallies run in correct order on return-from-inner-handler.
import asyncio
from tpy import Int32

async def value(n: Int32) -> Int32:
    return n

async def fail() -> Int32:
    raise ValueError("inner")

async def go() -> Int32:
    try:
        try:
            x = await fail()
            return x
        except ValueError:
            print("inner-handler")
            y = await value(Int32(5))
            return y
        finally:
            print("inner-finally")
    finally:
        print("outer-finally")

def main() -> None:
    print(asyncio.run(go()))

main()
