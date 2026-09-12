# Calling an @overload group with a kwarg that no overload accepts should
# produce a targeted "unexpected keyword argument" diagnostic, matching the
# single-overload path's behavior. Before, the multi-overload path silently
# rejected every candidate and emitted a bare "No matching @overload" error
# that hid the real cause from the user.
from tpy import int32, dispatch


@dispatch
def pick(x: int32, *, mode: str = "x") -> str:
    return mode + ":" + str(x)


@dispatch
def pick(x: int32, *, count: int32 = 1) -> int32:
    return x + count


def main() -> None:
    pick(int32(1), bogus="oops")  # tpyc: error(/got unexpected keyword argument 'bogus'/)


main()
