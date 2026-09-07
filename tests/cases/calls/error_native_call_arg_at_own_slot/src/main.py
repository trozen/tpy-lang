# A CALL rvalue at a native function's `Own` parameter: the native argument
# loop renders a value call bare in place, which an owning slot cannot take.
from tpy import Own
from tpy.extern import native


@native("my_ns::sink")
def sink(b: Own[bytes]) -> None: ...


def f(v: bytes) -> None:
    # The argument is a call rvalue at an owning native slot.
    sink(bytes(v))  # tpyc: error(/expr\.call:call\.native_arg\.own/)


def main() -> None:
    f(b"ab")


main()
