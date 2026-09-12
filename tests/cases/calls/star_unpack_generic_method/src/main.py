# *list unpack into a generic vararg METHOD (not just a free function).
# Methods route through _infer_method_subst_with_seed / the method-overload
# probe, which previously had no TpyStarUnpack handler and crashed with
# "Unknown expression type: TpyStarUnpack".
from tpy import int32, nocopy


@nocopy
class Box[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = v


class Pile:
    def total[T](self, *boxes: Box[T]) -> int32:
        n: int32 = 0
        for b in boxes:
            n += 1
        return n


def main() -> None:
    p = Pile()
    items: list[Box[int32]] = []
    items.append(Box(1))
    items.append(Box(2))
    items.append(Box(3))
    print("list unpack:", p.total(*items))


main()
