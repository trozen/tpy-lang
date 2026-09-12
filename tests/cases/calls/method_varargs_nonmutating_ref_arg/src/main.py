# Method vararg analog of varargs_nonmutating_ref_arg: a method `*boxes: Box`
# whose body only reads, called from a method whose `b`, `c` come from const
# params. Routes through the shared _analyze_and_pack_varargs via methods.py,
# so this guards against the marking being free-function-only.
from tpy import int32, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Pile:
    def total(self, *boxes: Box) -> int32:
        n: int32 = 0
        for b in boxes:
            n += b.val
        return n

    def via_param(self, b: Box, c: Box) -> int32:
        return self.total(b, c)  # tpyc: ok


def main() -> None:
    p = Pile()
    x = Box(3)
    y = Box(4)
    print(p.via_param(x, y))


main()
