# A local bound from a call returning OWNING tuple storage (Own[tuple])
# owns its element storage: returning it as a borrow tuple would lift
# addresses into the dying local -- rejected, through aliases too.
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:
    return (v, Box(v))


def pick() -> tuple[Int32, Box]:
    t = make_pair(5)
    u = t
    return u  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
