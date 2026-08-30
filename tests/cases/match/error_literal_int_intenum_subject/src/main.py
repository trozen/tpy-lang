# An int literal pattern over an IntEnum subject. CPython matches here and TPy
# renders the equivalent `==` comparison, so this is a gap, not a type error --
# the diagnostic must say so, distinctly from the plain-Enum rejection.
from enum import IntEnum


class Level(IntEnum):
    Low = 0
    High = 1


def describe(v: Level) -> str:
    match v:
        case 1:  # tpyc: error(/IntEnum subject type 'Level' is not yet implemented/)
            return "high"
        case _:
            return "other"


def main() -> None:
    print(describe(Level.Low))


main()
