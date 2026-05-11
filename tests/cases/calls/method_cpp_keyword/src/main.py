# Method names that collide with C++ reserved words (`double`, `delete`,
# `new`, ...) must be name-mangled consistently at declaration AND call
# site. Previously the declaration emitted the raw keyword (invalid C++)
# while call sites applied the mangling, producing two C++ errors.
from tpy import Int32


class Box:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n

    def double(self) -> Int32:
        return self.n * 2

    def delete(self) -> None:
        self.n = 0


def main() -> None:
    b = Box(5)
    print(b.double())
    b.delete()
    print(b.n)


main()
