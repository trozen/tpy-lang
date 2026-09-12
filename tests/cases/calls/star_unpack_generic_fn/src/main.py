# Generic vararg + Fn/Callable param + *list unpack. Exercises the Phase-2
# inference path: the *unpack supplies the only evidence for T, which must
# bind so the lambda's Callable[[Box[T]], int32] hint can concretize. Before
# the fix, the variadic param's packed Span[readonly[Box[T]]] type was matched
# against the unpacked element type Box[int32] and silently failed, leaving T
# unbound ("Lambda parameter types cannot be inferred without context").
from tpy import int32, nocopy
from typing import Callable


@nocopy
class Box[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = v


def proc[T](fn: Callable[[Box[T]], int32], *xs: Box[T]) -> int32:
    total: int32 = 0
    for x in xs:
        total += fn(x)
    return total


def main() -> None:
    items: list[Box[int32]] = []
    items.append(Box(2))
    items.append(Box(3))
    items.append(Box(5))
    print(proc(lambda b: b.val, *items))


main()
