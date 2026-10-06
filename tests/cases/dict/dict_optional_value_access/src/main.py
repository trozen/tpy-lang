# Element access on `dict[K, P | None]`: parallel shape to list[P|None]
# but via dict subscript. Container value slot is `optional<P>` (storage
# form); consumer sites lift via `optional_to_ptr`.
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
    d: dict[str, P | None] = {"a": P(int32(10)), "b": None, "c": P(int32(30))}
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


def popped() -> None:
    d: dict[str, P | None] = {"a": P(10), "c": P(30)}
    # pop(k) hands back the stored Optional by value: the binding holds the
    # popped object, which left the dict.
    o = d.pop("c")  # tpyc: ok
    if o is not None:
        o.x += 1
        print("pop", o.x, len(d), "c" in d)


main()
popped()
