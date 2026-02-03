from typing import Protocol


class NotAProtocol:
    x: int


class Child(NotAProtocol, Protocol):  # tpyc: error(/not a defined protocol/)
    pass
