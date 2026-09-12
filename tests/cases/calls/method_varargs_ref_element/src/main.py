# Reference-type *args on a method (`*boxes: Box`). Goes through the
# tpy::varargs<T> indirect-mode codegen path. Uses named locals at the
# call site -- the typical pattern; rvalue temporaries also work
# (varargs_rvalue_ref_arg covers that path).
from tpy import int32


class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


class Pile:
    def total(self, *boxes: Box) -> int32:
        s: int32 = int32(0)
        for b in boxes:
            s += b.value
        return s


def main() -> None:
    p = Pile()
    print(p.total())
    a = Box(int32(1))
    b = Box(int32(2))
    c = Box(int32(3))
    print(p.total(a, b, c))


main()
