# A for-each over a container field of an element off an un-narrowed
# `Optional[list[P]]` rejects, located, like its narrowed and plain-list twins:
# the loan is two hops from a storage key (BUGS.md#iter-borrow-place-needs-hops).
from typing import Optional

from tpy import int32


class P:
    rows: list[int32]

    def __init__(self) -> None:
        self.rows = [1, 2]


def show(d: Optional[list[P]]) -> None:
    # the subject: iterating an element's field through the unproven receiver
    for v in d[0].rows:  # tpyc: error(/not yet supported.*foreach.iter_borrow_unplaceable/)
        print(v)


def main() -> None:
    show([P()])


main()
