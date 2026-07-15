# A raising Base.__del__ in an inheritance chain fail-fasts (aborts); the child
# body ran first (explicit super().__del__() keeps drop order, dropped by TPy
# since C++ auto-chains). CPython ignores the raise and continues, so this
# diverges by design (no_cpython).
from tpy import Int32


class Base:
    _b: Int32

    def __init__(self, b: Int32):
        self._b = b

    def __del__(self):
        print("base del", self._b)
        raise ValueError("base cleanup failed")  # tpyc: warning(/.raise. in .__del__. cannot propagate/)


class Child(Base):
    _c: Int32

    def __init__(self, b: Int32, c: Int32):
        super().__init__(b)
        self._c = c

    def __del__(self):
        print("child del", self._c)
        super().__del__()


def main():
    x = Child(1, 2)
    print("in main")


main()
