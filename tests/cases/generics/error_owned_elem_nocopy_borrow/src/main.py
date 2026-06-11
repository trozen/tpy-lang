# Canonicalizing a subscript's borrow form to storage form for owned-context
# inference must NOT make a borrow -> Own[T] silently legal for a @nocopy
# element: binding Owned[T]'s param to Box[int] (not Box[int]&) lets the
# existing copyability check reject the implicit copy with a clean error.
from tpy import Own
from tplib.box import Box


class Owned[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v


def build(src: list[Box[int]]) -> None:
    e = Owned(src[0])  # tpyc: error(/copy non-copyable|non-copyable.*Box/)
    print(e.v.get())


def main() -> None:
    src: list[Box[int]] = []
    src.append(Box(5))
    build(src)


main()
