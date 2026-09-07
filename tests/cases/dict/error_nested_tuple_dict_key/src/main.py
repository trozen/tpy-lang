# A dict keyed by a NESTED tuple: the value-tuple key family is the flat
# spelling only, so both the literal key and the membership needle reject.
from tpy import Int32


def nested() -> None:
    d: dict[tuple[tuple[Int32, Int32], Int32], str] = {((1, 2), 3): "a"}  # tpyc: error(/container_lit\.key\.tuple/)
    print(((1, 2), 3) in d)


def flat() -> None:
    d: dict[tuple[Int32, Int32], str] = {(1, 2): "a"}
    print((1, 2) in d, (9, 9) in d)


def main() -> None:
    nested()
    flat()


main()
