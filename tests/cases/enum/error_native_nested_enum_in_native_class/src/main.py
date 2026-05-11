# @native enum nested inside a @native class is also rejected; the diagnostic
# points at the top-level workaround (the C++ qname encodes the nesting).
from enum import Enum, auto
from tpy.extern import native


@native("ns::Container")
class Container:
    @native("ns::Container::Kind")
    class Kind(Enum):  # tpyc: error(/@native enum.*cannot be nested/)
        A = auto()
        B = auto()


def main() -> None:
    pass


main()
