# Every plain (non-Int) enum member is truthy, but the operand still has to be
# evaluated -- folding `if make():` to `true` would drop the call. A member read
# folds its value the same way and still evaluates.
from enum import Enum


class Color(Enum):
    RED = 1
    GREEN = 2


calls = 0


def make() -> Color:
    global calls
    calls += 1
    return Color.RED


def main() -> None:
    if make():
        print("if:", calls)

    if not make():
        print("unreachable")
    print("not:", calls)

    while make():
        break
    print("while:", calls)

    # A member read and a name fold their value the same way, and still evaluate.
    if Color.GREEN:
        print("member operand ok")
    c = Color.RED
    if c:
        print("name operand ok")
    print("total:", calls)


main()
