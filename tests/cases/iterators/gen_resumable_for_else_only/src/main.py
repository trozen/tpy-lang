# for-loop generator where the loop BODY has no `yield` but the `else`
# clause does (Phase D3). The prescan must register the loop's uid via the
# orelse suspension check, not just the body -- otherwise the CFG builder
# raises "no registered uid" and the generator falls back to legacy. break
# still skips the else.
from typing import Iterator
from tpy import Int32


def gen(xs: list[Int32], brk: Int32) -> Iterator[Int32]:
    yield 0
    for x in xs:
        if x == brk:
            break
    else:
        yield -1
    yield -2


def main() -> None:
    print("no break:")
    for v in gen([1, 2, 3], 99):
        print(v)
    print("break:")
    for v in gen([1, 2, 3], 2):
        print(v)


main()
