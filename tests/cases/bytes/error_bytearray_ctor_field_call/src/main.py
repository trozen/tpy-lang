# The bytearray constructor-field source that still rejects: a FREE CALL. The
# member-init arm covers the target-threaded TYPE constructor
# (`bytearray(...)`); a call to an ordinary function has no member-init render
# to thread, so it rejects.
from tpy import int32, Own


def make(n: int32) -> Own[bytearray]:
    return bytearray(n)


class H:
    buf: bytearray

    def __init__(self, n: int32) -> None:
        self.buf = make(n)  # tpyc: error(/ctor.mil_field.nominal.call/)


def main() -> None:
    h = H(3)
    print(len(h.buf))


main()
