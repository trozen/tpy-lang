# A comprehension whose loop var is a pointer-repr Optional[record] container
# element bound off a CONST source (a readonly / const-borrow param), the const
# twin of the mutable-source binding: the loop var reads the storage optional
# directly and a whole-optional read lifts through optional_to_ptr. `P` is
# `@nocopy`: every read here is through the const source, so a silent element
# copy would be a compile error rather than an invisible divergence.
from tpy import int32, nocopy, readonly


@nocopy
class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def peek(p: P | None) -> int32:
    return -1 if p is None else p.x


def from_readonly(items: readonly[list[P | None]]) -> None:
    # The narrowed field read off the const-bound storage optional.
    print([v.x if v is not None else -1 for v in items])  # tpyc: ok
    # ... and the whole-optional read at a const `P*` param slot.
    print([peek(v) for v in items])  # tpyc: ok


def from_inferred(items: list[P | None]) -> None:
    # Sema infers the const borrow here too (nothing mutates items).
    print([v.x if v is not None else -1 for v in items])  # tpyc: ok


def from_local() -> None:
    xs: list[P | None] = [P(1), None]
    print([v.x if v is not None else -1 for v in xs])  # tpyc: ok
    print(sorted({peek(v) for v in xs}))  # tpyc: ok


def main() -> None:
    xs: list[P | None] = [P(4), None, P(6)]
    from_readonly(xs)
    from_inferred(xs)
    from_local()


main()
