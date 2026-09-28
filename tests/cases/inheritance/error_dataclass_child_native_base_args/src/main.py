# A `@dataclass` child of an `@native` base whose constructor takes arguments
# is rejected like one over a TPy base: its synthesized `__init__` cannot build
# the base (LANGUAGE_FEATURES "Single class inheritance").
from dataclasses import dataclass

from tpy import int32
from tpy.extern import native


@native("CppCounter")
class Counter:
    value: int32

    def __init__(self, value: int32) -> None: ...


# the synthesized `__init__` leaves `Counter` to a default constructor it lacks
@dataclass
class Tally(Counter):  # tpyc: error(/the '__init__' synthesized by '@dataclass' for 'Tally' does not initialize parent class 'Counter', but 'Counter' cannot be constructed without arguments/)
    extra: int32


def main() -> None:
    t = Tally(3)
    print(t.extra)


main()
