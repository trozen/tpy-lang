# Own[T] inside a tuple LOCAL annotation is rejected: Own marks ownership
# transfer and is valid only in parameter and return types. A local owns its
# value regardless, so the Own is redundant -- drop it (or use copy() for an
# owned copy).
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def main() -> None:
    t: tuple[int, Own[Box]] = (1, Box(2))  # tpyc: error(/Own\[T\] is redundant in this variable type/)
    print(t[1].val)


main()
