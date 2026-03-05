# Error: @dynamic protocol in a union
from typing import Protocol, Sized
from tpy import dynamic

@dynamic
class Printable(Protocol):
    def display(self) -> None: ...

def func(items: Printable | Sized) -> None:  # tpyc: error(/@dynamic protocol/)
    pass
