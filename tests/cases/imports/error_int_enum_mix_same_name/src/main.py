# Arithmetic between two IntEnums sharing a name across modules is refused
# like any two IntEnums; the message tells them apart by module.
from enum import IntEnum
import mq


class Level(IntEnum):
    LO = 1


def main() -> None:
    print(Level.LO + mq.Level.LO)  # tpyc: error(/Cannot mix arithmetic between 'Level' and 'mq.Level'/)


main()
