# `Own` nested under an `Optional`-of-tuple (`tuple[..., Own[T]] | None`) is NOT
# covered by the scalar `Optional[Own[T]]` exemption: the per-element Own
# collapses to borrow form regardless, so it is rejected as redundant. Drop the
# Own (`tuple[..., T] | None`) -- an owning-call init still materializes a slot
# the local aliases.
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def main() -> None:
    t: tuple[int, Own[Box]] | None = (1, Box(2))  # tpyc: error(/Own\[T\] is redundant in this variable type/)
    if t is not None:
        print(t[1].val)


main()
