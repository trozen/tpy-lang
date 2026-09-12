# `bytes * n` on a NARROWED `Optional[bytes]`: the repeat operator pins its
# bytes operand through the resolved dunder's receiver slot, which the
# narrowed legs do not cover.
from tpy import int32


def build(body: bytes | None, n: int32) -> int32:
    if body is not None:
        # The narrowed optional is the repeat's left operand.
        return len(body * n)  # tpyc: error(/stmt\.return:binop\.shape\.\*/)
    return 0


def main() -> None:
    print(build(b"x", 2))


main()
