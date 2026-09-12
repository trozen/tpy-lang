# set literal assignment rejects subclass element type (covers PendingSetType path)
from tpy import int32
from dataclasses import dataclass


@dataclass(frozen=True)
class Base:
    val: int32


@dataclass(frozen=True)
class Child(Base):
    extra: int32


def main() -> None:
    s: set[Base] = {Child(int32(1), int32(2))}  # tpyc: error(/Type mismatch.*expected Base, got Child/)


main()
