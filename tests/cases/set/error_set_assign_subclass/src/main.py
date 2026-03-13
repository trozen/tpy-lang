# set literal assignment rejects subclass element type (covers PendingSetType path)
from tpy import Int32
from dataclasses import dataclass


@dataclass(frozen=True)
class Base:
    val: Int32


@dataclass(frozen=True)
class Child(Base):
    extra: Int32


def main() -> None:
    s: set[Base] = {Child(Int32(1), Int32(2))}  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
