# Unpacking a reference-type container *parameter* (list[T], passed by C++
# reference -> Ref[list[T]]) into *args. Auto-readonly inference collapses
# both `take_mut` and `take_ro` to `varargs<const Box>`, so the source
# borrows const in both cases (the explicit readonly slot was already const).
from tpy import int32, readonly, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def take_mut(*items: Box) -> int32:
    n: int32 = 0
    for b in items:
        n += b.val
    return n


def take_ro(*items: readonly[Box]) -> int32:
    n: int32 = 0
    for b in items:
        n += b.val
    return n


def via_mut(xs: list[Box]) -> int32:
    return take_mut(*xs)


def via_ro(xs: list[Box]) -> int32:
    return take_ro(*xs)


def main() -> None:
    items: list[Box] = []
    items.append(Box(10))
    items.append(Box(20))
    print(via_mut(items))
    print(via_ro(items))


main()
