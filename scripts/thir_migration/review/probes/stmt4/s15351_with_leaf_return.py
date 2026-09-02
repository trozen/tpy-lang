from tpy import Int32
class CM:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def __enter__(self) -> Int32:
        return self.n
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(self.n)

async def step(n: Int32) -> Int32:
    return n + 1

async def f(n: Int32) -> Int32:
    n = await step(n)
    if n > 0:
        with CM(n) as base:
            return base + 1
    return n

def main() -> None:
    pass
main()
