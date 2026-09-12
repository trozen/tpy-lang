# A bytes method result (`v.strip()`) passed straight to a user function's
# `Own[bytes]` parameter: it compiles, and `take` prints the stripped length.
from tpy import int32, Own


def take(b: Own[bytes]) -> int32:
    return len(b)


def f(v: bytes) -> None:
    # A bytes method result at an owning parameter.
    print(take(v.strip()))


def main() -> None:
    f(b"  hi  ")


main()
