# Error: cannot mix different IntEnum types in arithmetic
from enum import IntEnum

class Priority(IntEnum):
    Low = 0
    High = 1

class Level(IntEnum):
    Min = 0
    Max = 1

def main() -> None:
    x = Priority.Low + Level.Max  # tpyc: error(/Cannot mix arithmetic/)

main()
