# Reference-type *args on a method (`*boxes: Box`). Goes through the
# tpy::varargs<T> indirect-mode codegen path. Uses named locals at the
# call site -- the typical pattern; rvalue temporaries also work
# (varargs_rvalue_ref_arg covers that path).
from tpy import Int32


class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


class Pile:
    def total(self, *boxes: Box) -> Int32:
        s: Int32 = Int32(0)
        for b in boxes:
            s += b.value
        return s


def main() -> None:
    p = Pile()
    print(p.total())
    a = Box(Int32(1))
    b = Box(Int32(2))
    c = Box(Int32(3))
    print(p.total(a, b, c))


main()
