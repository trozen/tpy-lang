# Regression: set-comprehension over `Rc[T]` is rejected with the same
# copy-constructibility diagnostic that fires for set literals and for
# `s: set[Rc[T]] = set()` annotations. Covers the comprehension arm of
# `_validate_hashable_container_elem`. The restriction is a runtime
# limitation (ordered_set's internal index forces copy-constructible
# keys), not a language-level design choice.
from tpy import Int32
from tplib import Rc


def main() -> None:
    items: list[Rc[Int32]] = []
    items.append(Rc.new(Int32(1)))
    items.append(Rc.new(Int32(2)))
    s = {r.clone() for r in items}  # tpyc: error(/Rc\[Int32\].*non-copyable.*set element/)
    print(len(s))


main()
