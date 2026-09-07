# Conditions that are bool literals: `assert True` emits nothing, `assert False`
# raises with no message, and the False side of if/while keeps its dead block.
from tpy import Int32


def checked(n: Int32) -> Int32:
    assert True  # folds away entirely
    return n


def boom() -> None:
    assert False  # a bare assertion raise, never called below


def dead(n: Int32) -> Int32:
    if False:  # the constant-false branch
        n = n + 1
    return n


def spin(n: Int32) -> Int32:
    while False:  # the constant-false loop head
        n = n + 1
    return n


def main() -> None:
    print(checked(1), dead(2), spin(3))


main()
