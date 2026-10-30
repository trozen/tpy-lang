# A @nocopy field of a live object at an Own parameter would be copied: a
# @nocopy value can only be moved, never copied (docs/LANGUAGE_FEATURES.md
# "@nocopy Types"); a last-use local moves (calls/argument_sink, nocopy_box_move).
from tpy import Own, int32
from tplib.box import Box


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


class HB:
    def __init__(self, b: Own[Box[A]]) -> None:
        self.b = b


def eat_box(b: Own[Box[A]]) -> None:
    print("eat_box", b.get().v)


def main() -> None:
    hb = HB(Box(A(1)))
    eat_box(hb.b)  # tpyc: error(/cannot copy non-copyable type 'Box\[A\]' into owned storage/)
    print(hb.b.get().v)


main()
