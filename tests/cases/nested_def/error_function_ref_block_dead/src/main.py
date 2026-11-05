# Handing out a nested def by name after the block that bound it gets the
# block-dead read error, as calling it does.
from typing import Callable


def make(flag: bool) -> Callable[[], int]:
    xs = [1, 2, 3]
    if flag:
        def esc() -> int:
            return len(xs)
        print("inner", esc())
    xs.append(8)
    return esc  # tpyc: error(/'esc' is not readable after the 'if' block/)


def main() -> None:
    print(make(False)())


main()
