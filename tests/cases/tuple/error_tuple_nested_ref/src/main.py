# A non-value member nested inside an inner tuple is rejected on return too --
# the same flat-conversion codegen gap as the yield path.
class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def f(b: Box) -> tuple[int, tuple[int, Box]]:
    return (1, (2, b))  # tpyc: error(/nested inside another tuple is not yet supported/)


def main() -> None:
    pass


main()
