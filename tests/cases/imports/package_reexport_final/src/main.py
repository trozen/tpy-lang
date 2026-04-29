# Final[T] re-exported through a package __init__.py keeps its Final marker.
# Exercises the propagation by using the re-exported names as default
# parameter values -- which is gated on `is_final` being True.
from tpy import Int32
from pkg import VERSION, LIMIT


def banner(prefix: str = VERSION) -> str:
    return prefix


def cap(n: Int32 = LIMIT) -> Int32:
    return n


def main() -> None:
    print(banner())
    print(banner("custom"))
    print(cap())
    print(cap(Int32(3)))


main()
