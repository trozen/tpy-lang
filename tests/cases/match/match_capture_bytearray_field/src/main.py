# A class pattern capturing a `bytearray` FIELD binds the capture to the
# field itself, as it does for a list field: the arm's binding takes the
# shared record-or-container gate. The capture is mutated and the object
# read back afterwards, so a copy would show.
from tpy import int32


class H:
    buf: bytearray

    def __init__(self) -> None:
        self.buf = bytearray(b"ab")


def main() -> None:
    h = H()
    match h:  # tpyc: ok
        case H(buf=v):
            v.append(99)
            print(len(v))
    print(len(h.buf))


main()
