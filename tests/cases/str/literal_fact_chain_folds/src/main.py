# A comparison chain over a `Literal` value in a VALUE position: inside the
# arm the whole chain is decided, so it renders as a bare bool.
from typing import Literal


def f(mode: Literal["r", "rb"]) -> None:
    if mode == "rb":
        # Both legs are already decided by the branch fact.
        b = mode == "rb" or mode == "r"
        print(b)


def main() -> None:
    f("rb")


main()
