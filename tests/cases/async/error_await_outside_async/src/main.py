# `await` outside an async def body is rejected with a hint pointing at asyncio.run.
def some_call() -> int:
    return 42

def main() -> None:
    x = await some_call()  # tpyc: error(/'await' is only allowed inside an `async def`/)
    print(x)

main()
