# A str/bytes FIELD write whose receiver is a borrow-returning CALL
# (`b.get().name = ...`) is rejected, for the reason the `Ptr`, tuple and
# container-element sibling cases state: the call result names no storage key
# a live view of that field was registered under, so the view outlives the
# buffer (BUGS.md#field-view-escape-needs-place). Workaround: bind the borrow
# to a local first (`m = b.get(); m.name = ...`) -- the binding borrows the
# payload, so the box still sees the write. The scalar field beside it keeps
# the receiver.
from tpy import Own, int32
from tplib import Box


class Inner:
    def __init__(self, name: Own[str], count: int32) -> None:
        self.name = name
        self.count = count


def zap(b: Box[Inner]) -> None:
    b.get().count = 9
    # the subject: the same receiver, a view-family field
    b.get().name = "replaced-through-the-call"  # tpyc: error(/field_write_shape/)


def main() -> None:
    b = Box(Inner("abcdefghijklmnopqrstuvwxyz", 1))
    m = b.get()
    v = m.name
    zap(b)
    print(v)


main()
