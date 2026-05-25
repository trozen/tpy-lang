# Generator with a `while ... else:` where the else clause yields (Phase D3).
# The CFG models break-vs-normal-exit, so `break` skips the else (which would
# otherwise yield -1) while normal loop exit runs it. Both arms exercised.
from typing import Iterator
from tpy import Int32


def gen(n: Int32, brk: Int32) -> Iterator[Int32]:
    i = 0
    while i < n:
        if i == brk:
            break
        yield i
        i += 1
    else:
        yield -1
    yield -2


def main() -> None:
    print("no break:")
    for v in gen(3, 99):
        print(v)
    print("break:")
    for v in gen(3, 1):
        print(v)


main()
