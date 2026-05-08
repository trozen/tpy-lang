# Coroutines are single-use, must-use values: storing the result of an
# async def call without consuming it is rejected.
async def sub() -> int:
    return 42

async def caller() -> int:
    coro = sub()  # tpyc: error(/Coroutine value from async def 'sub' must be consumed/)
    return 0

def main() -> None:
    print("ok")

main()
