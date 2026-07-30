# `cls` reaches every class-level member the class name reaches: Final
# constants, mutable ClassVar storage (read and write), an inherited constant,
# a sibling @staticmethod, and another @classmethod.
from typing import ClassVar, Final

from tpy import Int32


class Base:
    SCALE: Final[Int32] = 10


class Counter(Base):
    START: Final[Int32] = 3
    calls: ClassVar[Int32] = 0

    @classmethod
    def bump(cls) -> Int32:
        cls.calls += 1
        return cls.calls

    @classmethod
    def scaled_start(cls) -> Int32:
        # Inherited class constant, plus a sibling staticmethod through cls.
        return cls.double(cls.START * cls.SCALE)

    @classmethod
    def twice(cls) -> Int32:
        # A classmethod calling another classmethod through cls.
        return cls.bump() + cls.bump()

    @staticmethod
    def double(n: Int32) -> Int32:
        return n * 2


def main() -> None:
    print(Counter.bump())
    print(Counter.scaled_start())
    print(Counter.twice())
    print(Counter.calls)


main()
