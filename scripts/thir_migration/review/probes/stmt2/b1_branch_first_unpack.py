from tpy import int32, int64

async def step(n: int32) -> int32:
    return n + 1

def pair(n: int32) -> tuple[int32, int32]:
    return (n, n + 1)

async def f(n: int32) -> int32:
    if n > 2:
        a, b = pair(n)
        print(a + b)
    n = await step(n)
    return n

def main() -> None:
    pass
main()
