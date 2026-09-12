# Literals passed straight to print: a dict literal and a value tuple each wrap
# their spelled render in the matching printer.
from tpy import int32


def main() -> None:
    print({"a": 1})
    x: int32 = 1
    y: int32 = 2
    print((x, y))


main()
