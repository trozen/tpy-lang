# Literals passed straight to print: a dict literal and a value tuple each wrap
# their spelled render in the matching printer.
from tpy import Int32


def main() -> None:
    print({"a": 1})
    x: Int32 = 1
    y: Int32 = 2
    print((x, y))


main()
