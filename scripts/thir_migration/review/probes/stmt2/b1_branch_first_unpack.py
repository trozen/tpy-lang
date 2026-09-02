from tpy import Int32, Int64

async def step(n: Int32) -> Int32:
    return n + 1

def pair(n: Int32) -> tuple[Int32, Int32]:
    return (n, n + 1)

async def f(n: Int32) -> Int32:
    if n > 2:
        a, b = pair(n)
        print(a + b)
    n = await step(n)
    return n

def main() -> None:
    pass
main()
