# Expression-level error_return unwrap with reference semantics.
# Tests that @error_return calls returning non-value types preserve
# references: positive(p).updated() modifies the original p.
from tpy import int32, error_return, ReturnException
from typing import Self

class E(Exception, ReturnException):
    pass

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

    def updated(self) -> Self:
        self.x += 1
        self.y += 1
        return self

@error_return(E)
def positive(p: Point) -> Point:
    if p.x < 0 or p.y < 0:
        raise E
    return p

@error_return(E)
def modify(p: Point) -> Point:
    return positive(p).updated()

def main() -> None:
    p = Point(1, 2)
    try:
        result = modify(p)
    except E:
        print("error")
    else:
        print(result.x)
        print(result.y)
    # Original modified by .updated() through the error_return reference chain
    print(p.x)
    print(p.y)

    # Error case
    p2 = Point(-1, 2)
    try:
        modify(p2)
    except E:
        print("caught")
    print(p2.x)
    print(p2.y)

main()
