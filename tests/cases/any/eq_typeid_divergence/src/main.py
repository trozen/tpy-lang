# Regression test for the locked equality divergence from CPython:
# Any(1) == Any(1.0) is False in TPy (typeid-based) where CPython
# returns True. Pinned so the behaviour cannot drift toward CPython
# parity without a deliberate design change.

from typing import Any


def main() -> None:
    a: Any = 1
    b: Any = 1.0
    print(a == b)


main()
