# An explicit StrView generator param stays a zero-copy string_view borrow in
# the frame (the escape hatch from str/bytes owned capture); a temporary arg
# still triggers the borrow-from-temporary warning.
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
    # Temporary source: the view borrows it, so this warns. Constructed but not
    # iterated -- we only assert the diagnostic, not run the (opted-in) UB.
    g = lengths(make_tmp())  # tpyc: warning(/borrows from temporary/)


main()
