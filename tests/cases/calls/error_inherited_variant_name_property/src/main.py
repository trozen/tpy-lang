# A property setter whose C++ name is a variant name the class inherits
# from a base's @dispatch set is refused at the setter
# (docs/LANGUAGE_FEATURES.md, "@dispatch").
from tpy import dispatch, int32


class Base:
    @dispatch
    def set_m(self, x: int32) -> int32:
        return 1

    @dispatch
    def set_m(self, x: str) -> int32:
        return 2


class Child(Base):
    def __init__(self) -> None:
        self._v = 0

    @property
    def m__int32(self) -> int32:
        return self._v

    @m__int32.setter
    def m__int32(self, v: int32) -> None:  # tpyc: error(/'Child.set_m__int32' has the same name the compiler gives the inherited variant 'Base.set_m\(x: int32\)'; rename it/)
        self._v = v


def main() -> None:
    c = Child()
    c.m__int32 = 3
    print(c.set_m(3), c.m__int32)


main()
