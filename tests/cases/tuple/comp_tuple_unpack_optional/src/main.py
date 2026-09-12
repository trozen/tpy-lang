# Comprehension and generator-expression tuple-unpack over
# `list[tuple[P | None, ...]]`: source is storage form
# (`tuple<optional<P>, ...>`); unpack vars bind to the storage-form
# optional slots; consumer sites lift via `optional_to_ptr` when used as
# pointer-form `P | None`.
from tpy import int32


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def borrow(p: P | None) -> int32:
    if p is None:
        return int32(-1)
    return p.x


def main() -> None:
    items: list[tuple[P | None, int32]] = [
        (P(int32(1)), int32(10)),
        (None, int32(20)),
        (P(int32(3)), int32(30)),
    ]
    # Comprehension unpack: `p` passed to borrow-param
    results = [borrow(p) for p, n in items]
    for r in results:
        print(r)
    # Generator expression unpack (inlined genexpr path)
    total = sum(n for p, n in items)
    print(total)


main()
