# A str/bytes FIELD write through a @property-GETTER receiver is rejected,
# for the reason the `Ptr`, tuple, element and call sibling cases state: the
# getter hop spells a field path (`h.val.name`) that names no storage key a
# live view of that field was registered under, so the demotion never fires
# and the view outlives the buffer
# (BUGS.md#field-view-escape-needs-place). Unlike the other four the tracker
# does not even fall back to the coarse receiver mark here -- a field path IS
# spellable, just the wrong one.
# Workaround: bind the getter result to a local first (`r = h.val;
# r.name = ...`) -- the binding borrows the returned record, so the owner
# still sees the write and a view held off `r` is demoted at it.
# The scalar field beside it keeps the receiver.
from tpy import Own, int32


class Inner:
    def __init__(self, name: Own[str], count: int32) -> None:
        self.name = name
        self.count = count


class Holder:
    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner

    @property
    def val(self) -> Inner:
        return self.inner


def zap(h: Holder) -> None:
    h.val.count = 9
    # the subject: the same receiver, a view-family field
    h.val.name = "replaced-through-the-getter"  # tpyc: error(/field_write_shape/)


def main() -> None:
    h = Holder(Inner("abcdefghijklmnopqrstuvwxyz", 1))
    m = h.inner
    v = m.name
    zap(h)
    print(v)


main()
