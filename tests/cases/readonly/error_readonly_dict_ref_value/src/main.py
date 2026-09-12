# A reference-type value read from a readonly dict stays readonly, so passing
# it where a mutable is required is rejected (readonly is not over-unwrapped).
from tpy import readonly, int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def bump(b: Box) -> None:
    b.val += 1


def peek(d: readonly[dict[str, Box]]) -> None:
    bump(d["k"])  # tpyc: error(/readonly\[Box\] as mutable Box|cannot/)


def main() -> None:
    d: dict[str, Box] = {"k": Box(1)}
    peek(d)


main()
