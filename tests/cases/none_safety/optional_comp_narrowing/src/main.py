# Test Optional narrowing in ternary expressions inside comprehensions
from typing import Optional
from tpy import int32

class Foo:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __repr__(self) -> str:
        return f"Foo({self.x})"

def main() -> None:
    items: list[Optional[Foo]] = [Foo(1), None, Foo(3)]

    # Field access on narrowed Optional in comprehension ternary
    xs = [item.x if item is not None else -1 for item in items]
    print(xs)

    # Method call on narrowed Optional in comprehension ternary
    reprs = [repr(item) if item is not None else "none" for item in items]
    print(reprs)

main()
