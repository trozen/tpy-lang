# A nullable owned-record tuple parameter is a by-value optional, so a
# NAME passed to it would copy the records the caller's binding holds
# where Python shares them (the callee's write would not reach `t`):
# refused, as no transfer row renders it yet.
from tpy import int32, Own


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def owned(k: bool) -> tuple[Own[Box], int32] | None:
    if k:
        return None
    return (Box(1), 2)


def bump(t: tuple[Own[Box], int32] | None) -> None:
    if t is not None:
        t[0].n += 10


def main() -> None:
    t = owned(False)
    bump(t)  # tpyc: error(/call.arg_shape/)
    if t is not None:
        print(t[0].n)


main()
