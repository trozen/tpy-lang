# Inverse guard: a nested def (and a lambda) that only READ self must not
# de-const the enclosing method -- the snapshot pins the inferred `const`
# on both methods, and the lambda/nested-def captures spell `this`.
from typing import Callable
from tpy import Int32


def apply(f: Callable[[Int32], Int32], v: Int32) -> Int32:
    return f(v)


class C:
    n: Int32

    def __init__(self) -> None:
        self.n = 42

    def peek(self, k: Int32) -> Int32:
        def get(x: Int32) -> Int32:
            return x + self.n

        return get(k)

    def scaled(self, k: Int32) -> Int32:
        return apply(lambda x: x * self.n, k)


def main() -> None:
    c = C()
    print(c.peek(8))
    print(c.scaled(2))


main()
