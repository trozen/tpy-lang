# Span's constructors take their arguments positionally only (`/`), as the
# CPython stand-in's do: a keyword names no parameter it can bind.
from tpy import Span, int32


def count(sp: Span[int32]) -> int32:
    return len(Span(source=sp))  # tpyc: error(/No matching overload for 'Span' with argument types \(source=Span\[int32\]\)/)


def main() -> None:
    xs = [1, 2, 3]
    print(count(xs))


main()
