# A method implementing a @dynamic protocol slot may not be a @dispatch set
# (docs/LANGUAGE_FEATURES.md, "@dispatch"): the slot is one virtual.
from typing import Protocol
from tpy import dispatch, dynamic, int32


@dynamic
class Scaler(Protocol):
    def scale(self, k: float) -> int32: ...


class Base(Scaler):
    def __init__(self) -> None:
        pass

    def scale(self, k: float) -> int32:
        return 1


class Child(Base):
    def __init__(self) -> None:
        super().__init__()

    @dispatch
    def scale(self, k: float) -> int32:  # tpyc: error(/'Child.scale' implements @dynamic protocol method 'Scaler.scale' and cannot be a @dispatch set/)
        return 2

    @dispatch
    def scale(self, k: int) -> int32:
        return 3


def main() -> None:
    print(Child().scale(1.0))


main()
