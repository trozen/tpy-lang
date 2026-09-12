"""
Bare literal arguments to generic factories / constructors / methods reach
codegen as concrete types, not as literal-value C++ template args like
Box<3.14>(...), Rc<7>::new_(...), or method<3.14>(...). All four sites that
bind inferred_type_args (overload, single function, record ctor, method)
share one resolution helper; this guards against regressions on any of them.
"""
from tplib.box import Box
from tplib.rc import Rc
from tpy import float64


class Converter:
    def identity[U](self, val: U) -> U:
        return val


def pair[T](a: T, b: T) -> None:
    pass


def main() -> None:
    # Symmetric inference: literal first, concrete second. T promotes from
    # FloatLiteralType to float64 (mirrors the long-standing IntLiteralType
    # promote rule).
    pair(1.5, float64(2.5))
    pair(float64(3.5), 4.5)

    # Generic record constructor: int and float literals.
    bi = Box(42)
    bf = Box(2.71)
    print(bi.get())
    print(bf.get())

    # Generic static-method factory: int and float literals.
    ri = Rc.new(7)
    rf = Rc.new(3.14)
    print(ri.get())
    print(rf.get())

    # Generic method on a non-generic class: int and float literals.
    c = Converter()
    print(c.identity(42))
    print(c.identity(2.5))


main()
