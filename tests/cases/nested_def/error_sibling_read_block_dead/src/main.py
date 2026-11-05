# A nested def reading a def bound inside an earlier block -- on every path
# through it -- gets the block-dead read error, as a read in the enclosing
# body does: each def is a local of its own arm.
from typing import Callable


def make(flag: bool) -> Callable[[], int]:
    base = [7]
    if flag:
        def helper() -> int:
            return base[0] + 1
    else:
        def helper() -> int:
            return base[0] + 2

    def esc() -> int:
        return helper()  # tpyc: error(/'helper' is not readable after the 'if' block/)
    return esc


def main() -> None:
    print(make(True)())


main()
