# Regression: `set[Rc[T]]` (and `dict[Rc[T], V]`) rejected at sema today,
# but with a misleading diagnostic -- it says "dict key" for a set element
# and suggests `@dataclass(frozen=True)` even though `Rc.__hash__` exists.
# Pinning the current message so the BUGS.md fix lands together with the
# message correction; see BUGS.md "set[Rc[T]] and dict[Rc[T], V] are
# inconsistently rejected ...".
from tpy import Int32
from tplib import Rc


class Key:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

    def __hash__(self) -> Int32:
        return self.value

    def __eq__(self, other: Key) -> bool:
        return self.value == other.value


def main() -> None:
    a = Rc.new(Key(1))
    b = Rc.new(Key(2))
    s: set[Rc[Key]] = {a.clone(), b.clone()}  # tpyc: error(/Type 'Rc\[Key\]' cannot be used as a dict key/)
    print(len(s))


main()
