# An owned `bytes` NAME at a user method's `Own[bytes]` parameter: that is a
# real by-value C++ slot, so the copy temp must be hoisted rather than the
# name passed bare.
from tpy import int32, Own


class Sink:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def put(self, b: Own[bytes]) -> None:
        self.n += len(b)


def feed(s: Sink, src: bytes) -> int32:
    owned: bytes = bytes(src)
    # The owned name lands at a by-value Own[bytes] slot.
    s.put(owned)  # tpyc: error(/expr\.method_call:method\.arg_shape/)
    return s.n


def main() -> None:
    print(feed(Sink(), b"ab"))


main()
