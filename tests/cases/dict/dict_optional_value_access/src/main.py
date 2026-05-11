# Element access on `dict[K, P | None]`: parallel shape to list[P|None]
# but via dict subscript. Container value slot is `optional<P>` (storage
# form); consumer sites lift via `optional_to_ptr`.
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def borrow(p: P | None) -> Int32:
    if p is None:
        return Int32(-1)
    return p.x


def main() -> None:
    d: dict[str, P | None] = {"a": P(Int32(10)), "b": None, "c": P(Int32(30))}
    # Subscript-then-access
    if d["a"] is not None:
        print(d["a"].x)
    # Var-decl from subscript
    a = d["a"]
    if a is not None:
        print(a.x)
    # For-loop binding from dict.values()
    for v in d.values():
        print(borrow(v))


main()
