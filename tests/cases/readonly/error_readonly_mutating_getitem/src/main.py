# A subscript through a mutating `__getitem__` is a non-readonly method call, so
# a readonly receiver rejects it like `g.method()`.
from tpy import int32, readonly


class Counts:
    d: dict[str, int32]

    def __init__(self) -> None:
        self.d = {}

    @readonly(False)
    def __getitem__(self, k: str) -> int32:
        if k not in self.d:
            self.d[k] = 0
        return self.d[k]


def peek(c: readonly[Counts], k: str) -> int32:
    return c[k]  # tpyc: error(/non-readonly method '__getitem__' on readonly reference/)


def main() -> None:
    print(peek(Counts(), "a"))


main()
