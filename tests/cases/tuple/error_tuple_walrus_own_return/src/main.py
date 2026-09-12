# A walrus bound from an owning-tuple call carries the owning-storage fact
# (the Own peel matches the VarDecl binding), so returning it through an
# alias is rejected like the VarDecl form.
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def make_pair(v: int32) -> Own[tuple[int32, Box]]:
    return (v, Box(v))


def pick() -> tuple[int32, Box]:
    if (t := make_pair(5))[0] > 0:
        u = t
        return u  # tpyc: error(/storage owned by the function/)
    raise RuntimeError("unreachable")


def main() -> None:
    pass


main()
