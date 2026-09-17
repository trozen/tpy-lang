# A str/bytes FIELD write through a CONTAINER-ELEMENT receiver is rejected,
# for the reason the `Ptr` and tuple sibling cases state: the element names no
# storage key a live view of that field was registered under, so the view
# outlives the buffer (BUGS.md#field-view-escape-needs-place). One rung, three
# spellings -- a list element, a dict value, and a `*args` pack subscript.
# Workaround: bind the element to a local first (`r = rows[0]; r.name = ...`)
# -- the binding borrows the element, so the container still sees the write.
# The scalar field beside it keeps the receiver.
from tpy import Own, int32


class Inner:
    def __init__(self, name: Own[str], count: int32) -> None:
        self.name = name
        self.count = count


def zap(rows: list[Inner]) -> None:
    rows[0].count = 9
    # the subject: the same receiver, a view-family field
    rows[0].name = "replaced-through-the-element"  # tpyc: error(/field_write_shape/)


def main() -> None:
    rows = [Inner("abcdefghijklmnopqrstuvwxyz", 1)]
    r = rows[0]
    v = r.name
    zap(rows)
    print(v)


main()
