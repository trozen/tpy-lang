# The shape adjacent to capturing an Array field: capturing a `bytearray`
# field. The capture predicate admits it on the reference axis, but the arm's
# binding machinery downstream has no bytearray rung, so the match still
# rejects as a whole -- pinned so widening the predicate alone cannot be read
# as having opened this shape.
from tpy import int32


class H:
    buf: bytearray

    def __init__(self) -> None:
        self.buf = bytearray(b"ab")


def main() -> None:
    h = H()
    match h:  # tpyc: error(/not yet supported.*stmt.match/)
        case H(buf=v):
            print(len(v))


main()
