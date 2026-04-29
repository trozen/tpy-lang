# f-string interpolation on Any goes through __str__ (which dispatches
# to the per-type str slot).

from typing import Any


def main() -> None:
    a: Any = 42
    b: Any = "hello"
    c: Any = True
    print(f"a={a} b={b} c={c}")


main()
