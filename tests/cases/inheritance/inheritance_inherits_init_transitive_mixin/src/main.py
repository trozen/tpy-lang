# Transitive multi-base mixin inheritance: Child uses inherits_init_from to
# pull Base's __init__ via the mixin shape, then GrandChild inherits via the
# transitive `has_init` set on Child by the same mechanism.
from tpy import Int32


class Base:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Mixin:
    def hello(self) -> str:
        return "hi"


class Child(Base, Mixin):
    pass


class GrandChild(Child):
    pass


def main() -> None:
    c = Child(Int32(7))
    print(c.x)
    print(c.hello())

    g = GrandChild(Int32(13))
    print(g.x)
    print(g.hello())


main()
