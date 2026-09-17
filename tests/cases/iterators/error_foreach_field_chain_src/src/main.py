# A pure FIELD chain as a for-each iterable (`d.shelf.rows`, `self.inner.boxes`)
# is two hops from a storage key the borrow tracker can spell, so the iteration's
# loan has no key and `d.shelf.rows.append(...)` inside the loop would silently
# reallocate the very vector the iteration points into
# (BUGS.md#iter-borrow-place-needs-hops). Its subscript sibling
# (`d.shelf.rows[i]`) is pinned by error_foreach_unplaceable_chain_src; the
# ONE-hop field read (`sh.rows`) compiles and is checked, which is also the
# workaround: bind the intermediate record first.
# The method position below takes the same reject and is unannotated only
# because the compile stops at the first error.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Shelf:
    rows: list[Box]

    def __init__(self) -> None:
        self.rows = [Box(1)]


class Depot:
    shelf: Shelf

    def __init__(self) -> None:
        self.shelf = Shelf()

    # method: the same chain spelled off `self`
    def bump_self(self) -> None:
        for b in self.shelf.rows:
            b.n += 1


def bump(d: Depot) -> None:
    for b in d.shelf.rows:  # tpyc: error(/foreach.iter_borrow_unplaceable/)
        b.n += 5


def main() -> None:
    bump(Depot())


main()
