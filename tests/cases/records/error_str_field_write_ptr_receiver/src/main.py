# A str/bytes FIELD write through a `Ptr` receiver is rejected, although the
# scalar field beside it writes through the same receiver: a one-hop field read
# binds a VIEW of the field's buffer, and a write through a pointer names no
# storage key that view was registered under, so the view would read freed
# memory with no diagnostic (BUGS.md#field-view-escape-needs-place). The same
# reject covers a `Ptr` held in a record FIELD (`self.p.name = ...`) -- one
# receiver family, one rung -- and the tuple-element receiver has its own case.
from tpy import Own, Ptr, int32, take_ptr


class Inner:
    def __init__(self, name: Own[str], count: int32) -> None:
        self.name = name
        self.count = count


def zap(p: Ptr[Inner]) -> None:
    p.count = 9
    # the subject: the same receiver, a view-family field
    p.name = "replaced-through-the-pointer"  # tpyc: error(/field_write_shape/)


def main() -> None:
    i = Inner("abcdefghijklmnopqrstuvwxyz", 1)
    v = i.name
    zap(take_ptr(i))
    print(v)


main()
