# A lambda building a new list at a Callable slot whose bare list return is a
# borrow is refused naming the owning spelling, as a def's `return []` is.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", "A callable's return
# reads as a def's" (a callable that builds its result is spelled Own[R]).
from typing import Callable


def main() -> None:
    # The subject: a pending list literal at a borrow return.
    mk: Callable[[], list[int]] = lambda: []  # tpyc: error(/this lambda builds a new list\[int\], but Callable\[\[\], list\[int\]\] returns a borrow; declare Callable\[\[\], Own\[list\[int\]\]\]/)
    print(len(mk()))


main()
