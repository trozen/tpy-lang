# A rebind slot reserved while emitting a generator's lambda body must be
# declared INSIDE that lambda -- the enclosing function prologue is out of scope
# there. Covers the while and for lambda shapes. The cross-scope shape (declared
# before the loop, rebound inside) has no sound home and is rejected instead --
# see error_gen_rebind_slot_crosses_lambda.
from tpy import Int32
from typing import Iterator


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 1


def decl_inside_while(n: Int32) -> Iterator[Int32]:
    i = 0
    while i < n:
        p = Point(0)
        p = Point(i)
        yield p.x
        i += 1


def decl_inside_for(xs: list[Int32]) -> Iterator[Int32]:
    for v in xs:
        p = Point(0)
        p = Point(v)
        yield p.x


def alias_holds_across_rebind(n: Int32) -> Iterator[Int32]:
    i = 0
    while i < n:
        p = Point(i)
        alias = p
        p = Point(100)
        alias.bump()
        yield alias.x
        i += 1


def main() -> None:
    for got in decl_inside_while(3):
        print("while:", got)

    for got in decl_inside_for([1, 2, 3]):
        print("for:", got)

    for got in alias_holds_across_rebind(2):
        print("alias:", got)


main()
