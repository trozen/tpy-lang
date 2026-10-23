# A binding's omitted `None` default at an Optional parameter is filled, so it
# rejects as `opt(1, None)` does (BUGS.md#native-arg-filled-none-own-str-default).
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import int32
from tpy.extern import native


@native("nb::opt")
def opt(x: int32, y: int32 | None = None) -> int32: ...


def main() -> None:
    # The filled `None` meets the native-argument lowering's gap.
    print(opt(1))  # tpyc: error(/not yet supported/)


main()
