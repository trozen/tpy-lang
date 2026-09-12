# Regression: `dict[Rc[T], V]` is rejected at sema with the same
# copy-constructibility message that fires for `set[Rc[T]]`. The
# restriction is a runtime limitation -- ordered_map's std::unordered_map-
# based index stores keys in pair<const K, V>, forcing copy-construction
# of the key, not a language-level design choice. Annotation alone
# triggers the check (covers the `dict()` + subscript-assign bypass path
# too). The dict[K, Rc[T]] (move-only *value*) path already works.
from tpy import int32
from tplib import Rc


def main() -> None:
    d: dict[Rc[int32], int32] = {}  # tpyc: error(/Rc\[int32\].*non-copyable.*dict key/)
    a = Rc.new(int32(1))
    d[a.clone()] = 1
    print(len(d))


main()
