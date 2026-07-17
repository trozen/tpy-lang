# @hotpath parses on free functions and methods and has no effect on codegen yet;
# the generated C++ must match an undecorated equivalent.
from tpy import hotpath


@hotpath
def scale(x: int) -> int:  # tpyc: ok
    return x * 2


class Counter:
    count: int

    def __init__(self) -> None:
        self.count = 0

    @hotpath
    def bump(self, n: int) -> None:  # tpyc: ok
        self.count += scale(n)


def main() -> None:
    c = Counter()
    c.bump(3)
    c.bump(4)
    print(c.count)


main()
