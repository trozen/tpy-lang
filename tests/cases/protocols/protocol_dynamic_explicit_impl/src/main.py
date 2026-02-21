# class Dog(Pet) with @dynamic Pet verifies conformance at definition site
from tpy import dynamic, Int32
from typing import Protocol

@dynamic
class Describable(Protocol):
    def describe(self) -> str:
        ...
    def id(self) -> Int32:
        ...

class Item(Describable):
    _name: str
    _id: Int32

    def __init__(self, name: str, id: Int32) -> None:
        self._name = name
        self._id = id

    def describe(self) -> str:
        return self._name

    def id(self) -> Int32:
        return self._id

def main() -> None:
    item = Item("Widget", 42)
    print(item.describe())
    print(item.id())

main()
