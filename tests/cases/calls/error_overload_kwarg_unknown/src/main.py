# Calling an @overload group with a kwarg that no overload accepts should
# produce a targeted "unexpected keyword argument" diagnostic, matching the
# single-overload path's behavior. Before, the multi-overload path silently
# rejected every candidate and emitted a bare "No matching @overload" error
# that hid the real cause from the user.
from typing import overload
from tpy import Int32


@overload
def pick(x: Int32, *, mode: str = "x") -> str:
    return mode + ":" + str(x)


@overload
def pick(x: Int32, *, count: Int32 = 1) -> Int32:
    return x + count


def main() -> None:
    pick(Int32(1), bogus="oops")  # tpyc: error(/got unexpected keyword argument 'bogus'/)


main()
