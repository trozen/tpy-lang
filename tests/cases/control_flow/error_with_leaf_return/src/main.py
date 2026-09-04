# A `return` nested in a suspension-free `with` inside an async body: the return
# renders through the frame return hook, which walks the skeleton finally stack
# rather than this with layer, so the __exit__ chain would be dropped.
from tpy import Int32


class CM:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __enter__(self) -> Int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        print("exit", self.n)


async def step(n: Int32) -> Int32:
    return n + 1


async def f(n: Int32) -> Int32:
    n = await step(n)
    with CM(n) as base:  # tpyc: error(/not yet supported.*with.leaf_return/)
        return base + 1


def main() -> None:
    pass


main()
