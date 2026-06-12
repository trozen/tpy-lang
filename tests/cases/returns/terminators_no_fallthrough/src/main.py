# Real terminators must not trip the missing-return error: `while True`
# with no loop-level break, and an `assert False` tail.
from tpy import Int32


def spin(n: Int32) -> Int32:
    while True:
        n = n + 1
        if n > 3:
            return n


def nested_break_ok(n: Int32) -> Int32:
    while True:
        for i in range(3):
            if i == n:
                break
        return n


def find_or_die(xs: list[Int32], v: Int32) -> Int32:
    for i in range(len(xs)):
        if xs[i] == v:
            return i
    assert False, "not found"


def finally_returns(n: Int32) -> Int32:
    try:
        n = n + 1
    finally:
        return n


def main() -> None:
    print(spin(0))
    print(nested_break_ok(2))
    print(find_or_die([5, 6, 7], 6))
    print(finally_returns(9))


main()
