# Every object a reassigned variable whose class inherits __del__ held runs
# the derived destructor and then the base's, exactly once per object -- the
# C++ destructor chain, which CPython does not run (see no_cpython.txt).
class Base:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __del__(self) -> None:
        print("~Base", self.tag)


class Child(Base):
    def __init__(self, tag: str) -> None:
        super().__init__(tag)

    def __del__(self) -> None:  # tpyc: warning(/Parent class has __del__\(\) which will be called automatically/)
        print("~Child", self.tag)


def test_inherit() -> None:
    c = Child("x")
    # Two rebinds: each superseded Child drops through the whole chain.
    c = Child("y")
    c = Child("z")
    print("alive:", c.tag)


def main() -> None:
    test_inherit()


main()
