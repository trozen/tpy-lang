# `element_access=<twin>` names the template method that performs the same
# element access without the bounds check; a twin taking different arguments
# than the subscript variant is an error, not a silent mismatched render.
from tpy import int32, Own
from tpy.extern import native, cpp_template


@native("my::Ring")
class Ring[T]:
    @cpp_template("{self}[static_cast<std::size_t>({0})] = {1}")
    def unchecked_set(self, index: int32, value: int32) -> None: ...

    @native("put", element_access=unchecked_set)
    def __setitem__(self, index: int32, value: Own[T]) -> None: ...  # tpyc: error(/names 'Ring.unchecked_set', whose parameters do not match the variant's/)


def main() -> None:
    print(0)


main()
