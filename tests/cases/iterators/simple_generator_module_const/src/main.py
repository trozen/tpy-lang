# Regression: a module-level constant access (mod.CONST) inside a simple
# (single-yield) generator's loop body. The simple-generator peephole emitted
# the loop condition + body with current_ns cleared, so mod.CONST fell out of
# the binding-based field-access dispatch and crashed codegen. Both the while
# and for peephole forms are covered.
from typing import Iterator
from tpy import int32
import caps


def upto_while(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        if i == caps.CAP:
            break
        yield i
        i += 1


def upto_for(n: int32) -> Iterator[int32]:
    for i in range(n):
        if i == caps.CAP:
            break
        yield i


def main() -> None:
    for x in upto_while(10):
        print(x)
    print("--")
    for x in upto_for(10):
        print(x)


main()
