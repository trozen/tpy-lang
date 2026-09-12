# An UNBOUND `BaseA.x` read of a @native field carrying a renamed C++ member:
# the unbound-self path never applies the rename, so the read has no correct
# spelling.
from tpy import int32
from tpy.extern import native, native_field


@native
class BaseA:
    x: int32 = native_field("m_x")


class Child(BaseA):
    def read(self) -> int32:
        # The receiver is the class, not an instance.
        return BaseA.x  # tpyc: error(/stmt\.return:field\.receiver_shape/)


def main() -> None:
    print(Child().read())


main()
