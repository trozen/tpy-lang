# A nested def may not rebind a name a def earlier in its block or an
# enclosing one bound: which def a closure holds would depend on the path
# taken. Here a closure handed out before the `if` holds the first `esc`.
# Lifting it is TODO.md "A nested `def` is a binding of a function
# local".
from typing import Callable


def before_block(out: list[Callable[[], int]], flag: bool) -> None:
    base = [7]

    def esc() -> int:
        return base[0] + 1
    out.append(esc)
    if flag:
        def esc() -> int:  # tpyc: error(/this inner definition rebinds the enclosing function's local 'esc'; TPy does not support this rebinding/)
            return base[0] + 2
        print("before_block inner", esc())


def main() -> None:
    out: list[Callable[[], int]] = []
    before_block(out, True)
    print(out[0]())


main()
