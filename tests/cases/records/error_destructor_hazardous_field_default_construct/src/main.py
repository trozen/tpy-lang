# Default-constructing a record with `__del__` + no `__init__` + a raw
# Ptr[T] field is rejected: the destructor would read indeterminate
# `_handle`. Companion to destructor_hazardous_field_no_init -- that
# test guards the codegen suppression (snapshot); this one guards the
# sema diagnostic at the call site.
from tpy import int32, Ptr


class Hazard:
    _handle: Ptr[int32]

    def __del__(self) -> None:
        print("dropping hazard")


def main() -> None:
    h = Hazard()  # tpyc: error(/cannot.*Hazard|not default-constructible|no default constructor/)
    print(h)


main()
