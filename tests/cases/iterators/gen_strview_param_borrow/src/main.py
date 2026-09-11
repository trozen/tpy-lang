# An explicit StrView generator param stays a zero-copy string_view borrow in
# the frame (the escape hatch from str/bytes owned capture); a temporary arg is
# hoisted into a named local of the calling block, so the borrow is safe and no
# borrow-from-temporary warning is owed.
from typing import Iterator
from tpy import StrView


def lengths(s: StrView) -> Iterator[int]:
    yield len(s)
    yield len(s)


def make_tmp() -> str:
    return "ab" + "cd"


def main() -> None:
    text = "hello"
    for n in lengths(text):  # outliving local source: borrow is safe, no warning
        print(n)
    # Temporary source: hoisted, so the view reads live storage on both pulls.
    g = lengths(make_tmp())  # tpyc: ok
    for n in g:
        print(n)


main()
