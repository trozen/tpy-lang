# Regression: `set[Rc[T]]` with the empty-set + `.add()` form -- the
# annotation alone is rejected by sema. Previously this path bypassed
# the literal-time check and surfaced as a multi-screen C++ template
# error from ordered_set internals. The restriction itself is a runtime
# limitation -- ordered_set's std::unordered_map-based index forces
# copy-constructible keys, not a language-level design choice. The
# dict[Rc[T], V] form is exercised by error_rc_dict_key.
from tpy import int32
from tplib import Rc


def main() -> None:
    s: set[Rc[int32]] = set()  # tpyc: error(/Rc\[int32\].*non-copyable.*set element/)
    a = Rc.new(int32(1))
    s.add(a.clone())
    print(len(s))


main()
