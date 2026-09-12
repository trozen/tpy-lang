# *list unpacking into a generic vararg with a reference-element type.
# Pinned the dispatch fix in tpyc/sema/calls.py:_infer_arg_types --
# without it, generic-T inference walked the TpyStarUnpack via the
# structural analyze_expr which had no handler and raised
# "Unknown expression type: TpyStarUnpack".
from tpy import int32, nocopy


@nocopy
class Box[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = v


def take_all[T](*boxes: Box[T]) -> int32:
    n: int32 = 0
    for b in boxes:
        n += 1
    return n


def main() -> None:
    items: list[Box[int32]] = []
    items.append(Box(1))
    items.append(Box(2))
    items.append(Box(3))
    print(take_all(*items))


main()
