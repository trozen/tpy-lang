# A nested def captured by an escaping closure escapes with it, so its
# nonlocal rebind is refused as in a directly escaping one.
from typing import Callable


def make() -> Callable[[], int]:
    base = 7

    def bump() -> int:  # tpyc: error(/nonlocal 'base' in escaping closure 'bump' is not supported \(nested function 'esc' escapes and captures it/)
        nonlocal base
        base += 1
        return base

    def esc() -> int:
        return bump()
    return esc


def main() -> None:
    print(make()())


main()
