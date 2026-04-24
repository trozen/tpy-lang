# The child writes BaseN.__init__(self, ...) calls in the opposite order from
# the base declaration. sema warns because C++ runs base constructors (and
# evaluates their argument expressions) in declaration order regardless of
# how the MIL is written; silently reordering would hide that from the reader.
from tpy import Int32


class Left:
    a: Int32

    def __init__(self, a: Int32) -> None:
        self.a = a


class Right:
    b: Int32

    def __init__(self, b: Int32) -> None:
        self.b = b


class Child(Left, Right):
    def __init__(self, a: Int32, b: Int32) -> None:
        Right.__init__(self, b)
        Left.__init__(self, a)  # tpyc: warning(/written out of declaration order/)


def main() -> None:
    c = Child(Int32(1), Int32(2))
    print(c.a)
    print(c.b)


main()
