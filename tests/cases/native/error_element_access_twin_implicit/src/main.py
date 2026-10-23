# A `__getitem__` returning a reference is implicitly a mutable / readonly
# pair; the diagnostic for a twin the readonly half cannot call names that
# implicit readonly-ness, not one the user did not write.
from tpy import int32
from tpy.extern import native, cpp_template


@native("my::Cell")
class Cell:
    n: int32


@native("my::Ring")
class Ring:
    @cpp_template("{self}.raw_mut({0})")
    def unchecked_get(self, index: int32) -> Cell: ...

    @native("at", element_access=unchecked_get)
    def __getitem__(self, index: int32) -> Cell: ...  # tpyc: error(/names 'Ring.unchecked_get', which does not accept a readonly receiver: __getitem__ is implicitly readonly/)


def main() -> None:
    print(0)


main()
