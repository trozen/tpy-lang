# Regression: set-comprehension over `Rc[T]` is rejected with the same
# copy-constructibility diagnostic that fires for set literals and for
# `s: set[Rc[T]] = set()` annotations. Covers the comprehension arm of
# `_validate_hashable_container_elem`. The restriction is a runtime
# limitation (ordered_set's internal index forces copy-constructible
# keys), not a language-level design choice.
from tpy import int32
from tplib import Rc


def main() -> None:
    items: list[Rc[int32]] = []
    items.append(Rc.new(int32(1)))
    items.append(Rc.new(int32(2)))
    s = {r.clone() for r in items}  # tpyc: error(/Rc\[int32\].*non-copyable.*set element/)
    print(len(s))


main()
