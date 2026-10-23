# `element_access=<twin>` names the twin by reference, so a twin defined below
# the variant is an error, as it is a NameError when CPython runs the decorator.
from tpy import int32, readonly
from tpy.extern import native, cpp_template


@native("my::Ring")
class Ring[T]:
    @native("at", element_access=unchecked_get)  # tpyc: error(/element_access names 'unchecked_get', which is not defined above '__getitem__' in class 'Ring'/)
    @readonly
    def __getitem__(self, index: int32) -> T: ...

    @cpp_template("{self}.raw({0})")
    @readonly
    def unchecked_get(self, index: int32) -> T: ...

    @native("size")
    @readonly
    def __len__(self) -> int32: ...


def main() -> None:
    print(0)


main()
