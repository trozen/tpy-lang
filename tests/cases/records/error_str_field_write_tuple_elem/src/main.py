# A str/bytes FIELD write through a TUPLE ELEMENT receiver is rejected, for the
# reason the `Ptr` sibling case states: a tuple element names no storage key a
# live view of that field was registered under, so the view outlives the buffer
# (BUGS.md#field-view-escape-needs-place). The scalar field beside it keeps the
# receiver, and the reject is the same whether the tuple is built at the call
# or bound to a local first.
from tpy import Own, int32


class Inner:
    def __init__(self, name: Own[str], count: int32) -> None:
        self.name = name
        self.count = count


def zap(t: tuple[Inner, int32]) -> None:
    t[0].count = 9
    # the subject: the same receiver, a view-family field
    t[0].name = "replaced-through-the-element"  # tpyc: error(/field_write_shape/)


def main() -> None:
    i = Inner("abcdefghijklmnopqrstuvwxyz", 1)
    v = i.name
    zap((i, 1))
    print(v)


main()
