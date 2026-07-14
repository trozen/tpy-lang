# TPy datetime behavior at edges where CPython 3.14 diverges, so no_cpython:
# these pin TPy's own (pre-3.14-matching) output as an exec-phase regression
# guard without coupling to a CPython version. strftime %Y/%G are NOT
# zero-padded on tiny years (3.14 pads to 4 digits); time.fromisoformat
# rejects "24:00" (3.14 accepts it as midnight). See docs/DATETIME_DESIGN.md.
from datetime import date, time


def main() -> None:
    print(date(1, 1, 1).strftime("%Y %G"))
    print(date(42, 5, 1).strftime("%Y %G"))
    try:
        print(repr(time.fromisoformat("24:00")))
    except ValueError:
        print("ValueError")


main()
