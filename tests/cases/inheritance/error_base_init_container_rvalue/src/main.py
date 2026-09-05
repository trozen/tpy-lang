# The shape adjacent to a container base-init arg: the same container as a
# CALL rvalue rather than a bare param name. The target-less base-init render
# is the bare name and can register no temp, so an rvalue keeps its own rung.
from tpy import Int32


class Base:
    buf: bytearray

    def __init__(self, b: bytearray) -> None:
        self.buf = b


class Child(Base):
    n: Int32

    # The ctor reject reports the enclosing `def` line, not the offending
    # call, so the annotation sits here.
    def __init__(self) -> None:  # tpyc: error(/not yet supported.*ctor.base_init/)
        super().__init__(bytearray(b"xy"))
        self.n = 1


def main() -> None:
    c = Child()
    print(len(c.buf), c.n)


main()
