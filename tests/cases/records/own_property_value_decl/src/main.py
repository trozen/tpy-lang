# Binding an `Own[...]`-returning @property to a local: the read renders as the
# getter call, so the decl OWNS the result (`std::vector<int32_t> snap =
# s.snapshot();`) instead of trying to alias a temporary. This is the bind-first
# workaround the by-value for-each fence names
# (BUGS.md#own-property-iterable-materialize).
# The copy is the point -- the getter hands back a fresh list -- so every section
# mutates the local and prints the owner to show the owner did NOT change.
from typing import Iterator

from tpy import int32, Own, StrView


class Snap:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [1]

    @property
    def snapshot(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._items:
            out.append(x)
        return out

    # method: the same decl spelled off `self`
    def widen(self) -> int32:
        mine = self.snapshot  # tpyc: ok
        mine.append(9)
        return len(mine)


# free function: the bare decl
def use(s: Snap) -> None:
    snap = s.snapshot  # tpyc: ok
    snap.append(2)
    print("free_fn:", len(snap), len(s._items))


# free function: the annotated decl takes the same owning family
def use_annotated(s: Snap) -> None:
    snap: list[int32] = s.snapshot  # tpyc: ok
    snap.append(3)
    print("annotated:", len(snap), len(s._items))


# generator: the frame holds the owned local across the suspension, which is why
# the for-each over the getter itself is fenced there
def drain(s: Snap) -> Iterator[int32]:
    snap = s.snapshot  # tpyc: ok
    snap.append(4)
    for x in snap:
        yield x


class Holder:
    label: str

    def __init__(self) -> None:
        self.label = "ab"

    @property
    def view(self) -> StrView:
        return self.label


# free function: the same workaround for the VIEW getter family, which the
# frame route fences alongside the Own one -- here the local is the view, so
# nothing is copied and the loop walks the field's own buffer
def use_view(h: Holder) -> None:
    v = h.view  # tpyc: ok
    n = 0
    for c in v:
        n += 1
        print("view_char:", c)
    print("view:", n)


def main() -> None:
    s = Snap()
    use_view(Holder())
    use(s)
    use_annotated(s)
    print("method:", s.widen(), len(s._items))
    for v in drain(s):
        print("frame:", v)
    print("owner:", len(s._items))


main()
