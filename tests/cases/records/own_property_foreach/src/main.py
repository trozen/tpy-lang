# An `Own[...]`-returning @property as a for-each iterable: the read is the
# getter call, so the loop takes the owning `auto __obj_N =` capture.
# The COPY is the subject: the getter builds a fresh list, so appending to the
# owner mid-loop must not change what the loop walks and appending to the
# snapshot must not reach the owner -- both are observed below, and CPython
# builds a fresh list too.
from tpy import int32, Own


class Snap:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [1, 2]

    @property
    def snapshot(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._items:
            out.append(x)
        return out

    # the spelled twin, whose render this read now shares
    def snapshot_m(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._items:
            out.append(x)
        return out

    # method body: the same iterable spelled off `self`
    def total(self) -> int32:
        n = 0
        for x in self.snapshot:  # tpyc: ok
            n += x
        return n


# free function: the iterable is the getter read; the owner grows mid-loop and
# the loop does not see it, because it walks the snapshot
def use(s: Snap) -> None:
    seen = 0
    for x in s.snapshot:  # tpyc: ok
        seen += 1
        s._items.append(99)
    print("free_fn:", seen, len(s._items))


# free function: the method twin, to show the two spellings agree
def use_method(s: Snap) -> None:
    seen = 0
    for x in s.snapshot_m():  # tpyc: ok
        seen += 1
    print("method:", seen)


def main() -> None:
    s = Snap()
    use(s)
    print("owner:", len(s._items))
    use_method(Snap())
    print("in_method:", Snap().total())


main()
