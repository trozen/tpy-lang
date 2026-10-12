# A function=True @native member through a Ptr keeps rejecting, while a renamed
# member through the same Ptr lowers (BUGS.md#ptr-receiver-template-member-rejects).
from tpy import int32, Ptr
from tpy.extern import native


@native("nb::Bag")
class Bag:
    @native("count")
    def count(self) -> int32: ...

    @native("nb::bag_size", function=True)
    def size(self) -> int32: ...


def total(p: Ptr[Bag]) -> int32:
    return p.count() + p.size()  # tpyc: error(/method\.marker\.deref\.builtin/)


def main() -> None:
    print(1)


main()
