# Final[T] re-exported through a package __init__.py keeps its Final marker.
# Exercises the propagation by using the re-exported names as default
# parameter values -- which is gated on `is_final` being True.
from tpy import int32
from pkg import VERSION, LIMIT


def banner(prefix: str = VERSION) -> str:
    return prefix


def cap(n: int32 = LIMIT) -> int32:
    return n


def main() -> None:
    print(banner())
    print(banner("custom"))
    print(cap())
    print(cap(int32(3)))


main()
