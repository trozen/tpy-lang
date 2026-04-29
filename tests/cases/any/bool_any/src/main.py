# Truthiness on raw Any dispatches through the to_bool slot and
# produces Python's bool() answer (0 falsy, 1 truthy, "" falsy, "x" truthy,
# None falsy).

from typing import Any


def main() -> None:
    a: Any = 0
    b: Any = 1
    c: Any = ""
    d: Any = "x"
    e: Any = None
    if a:
        print("a-true")
    else:
        print("a-false")
    if b:
        print("b-true")
    if not c:
        print("c-false")
    if d:
        print("d-true")
    if not e:
        print("e-false")


main()
