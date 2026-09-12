# Empty subclass with multi-base where only one parent has __init__.
# Mirrors CPython's MRO ctor lookup: the single init-bearing parent supplies
# the constructor; the mixin parent default-constructs.
from tpy import int32


class Base:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Greeter:
    def hello(self) -> str:
        return "hi"


class WithMixin(Base, Greeter):
    pass


# Sister-base order should not matter -- still picks Base's __init__.
class WithMixinReversed(Greeter, Base):
    pass


def main() -> None:
    a = WithMixin(int32(7))
    print(a.x)
    print(a.hello())

    b = WithMixinReversed(int32(13))
    print(b.x)
    print(b.hello())


main()
