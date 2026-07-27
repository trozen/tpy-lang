# A total=True field is always present, so `"a" in td` folds to True -- but the
# TypedDict operand still has to be evaluated. Folding `"a" in make()` away
# would drop the call; a name operand folds the same and still evaluates.
from typing import TypedDict

from tpy import Own


class TD(TypedDict):
    a: int


calls = 0


def make() -> Own[TD]:
    global calls
    calls += 1
    return TD(a=1)


def main() -> None:
    if "a" in make():
        print("in:", calls)

    if "a" not in make():
        print("unreachable")
    print("not in:", calls)

    # A name operand folds its value the same way and still evaluates.
    td = TD(a=3)
    if "a" in td:
        print("name operand ok")
    print("total:", calls)


main()
