# A nested def may not rebind a name a def earlier in the SAME block bound:
# `esc` already holds the first `helper`, which CPython would replace.
# Lifting it is TODO.md "A nested `def` is a binding of a function
# local".
from typing import Callable


def make() -> Callable[[], int]:
    base = [7]

    def helper() -> int:
        return base[0] + 1

    def esc() -> int:
        return helper()

    def helper() -> int:  # tpyc: error(/this inner definition rebinds the enclosing function's local 'helper'; TPy does not support this rebinding/)
        return base[0] + 2
    return esc


def main() -> None:
    print(make()())


main()
