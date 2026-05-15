# Regression: a tuple element / dict key that wraps an @nocopy type
# (here `tuple[Rc[T], V]`) hits the current sema gate the same way bare
# `set[Rc[T]]` does -- the non-copyable predicate recurses through
# TupleType.element_types so composite keys can't bypass it. The
# move-only-key restriction itself is a runtime limitation (ordered_set
# / ordered_map use std::unordered_map internally, whose pair<const K, V>
# is hostile to move-only K), not a language-level design choice.
from tpy import Int32
from tplib import Rc


def main() -> None:
    s: set[tuple[Rc[Int32], Int32]] = set()  # tpyc: error(/tuple\[Rc\[Int32\], Int32\].*non-copyable.*set element/)
    a = Rc.new(Int32(1))
    s.add((a.clone(), Int32(2)))
    print(len(s))


main()
