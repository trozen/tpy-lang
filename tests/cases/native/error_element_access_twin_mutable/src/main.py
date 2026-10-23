# The unchecked twin replaces the variant's call on the same receiver: the
# readonly half of an `@auto_readonly` getter cannot name a twin that only
# a mutable receiver may call.
from tpy import int32, auto_readonly
from tpy.extern import native, cpp_template


@native("my::Ring")
class Ring[T]:
    @cpp_template("{self}.raw_mut({0})")
    def unchecked_get(self, index: int32) -> T: ...

    @native("at", element_access=unchecked_get)
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...  # tpyc: error(/names 'Ring.unchecked_get', which does not accept a readonly receiver: __getitem__ takes a readonly receiver/)


def main() -> None:
    print(0)


main()
