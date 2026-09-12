# Regression: `set[Rc[T]]` is rejected at sema with a precise diagnostic
# pointing at the copy-constructibility requirement -- ordered_set's
# current std::unordered_map-based index stores keys in pair<const K, V>
# which forces copy-construction; Rc[T] (move-only) can't satisfy that.
# The same gate fires regardless of how the set is constructed (literal
# here; see error_rc_set_add for the `set()` + `.add()` form). The
# restriction is a runtime limitation, not a language design choice --
# a future runtime redesign could lift it.
from tpy import int32
from tplib import Rc


class Key:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    def __hash__(self) -> int32:
        return self.value

    def __eq__(self, other: Key) -> bool:
        return self.value == other.value


def main() -> None:
    a = Rc.new(Key(1))
    b = Rc.new(Key(2))
    s: set[Rc[Key]] = {a.clone(), b.clone()}  # tpyc: error(/Rc\[Key\].*non-copyable.*set element/)
    print(len(s))


main()
