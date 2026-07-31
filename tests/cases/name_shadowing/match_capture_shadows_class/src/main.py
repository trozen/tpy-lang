# A `case ... as NAME` capture whose name also names a module-level class: reads
# in the arm body must see the matched subject, not the class. The arm mutates
# through the capture and main() observes it afterwards, so an aliasing failure
# would surface as a wrong value instead of reading clean.
from typing import ClassVar

from tpy import Int32


class Registry:
    code: ClassVar[Int32] = 999


class Item:
    def __init__(self, code: Int32):
        self.code = code


class Other:
    def __init__(self, tag: Int32):
        self.tag = tag


def pick(v: Item | Other) -> Int32:
    match v:
        case Item() as Registry:  # tpyc: ok
            Registry.code += 1
            return Registry.code
        case Other() as o:
            return o.tag


def main() -> None:
    it = Item(6)
    print(pick(it))
    print(it.code)
    print(Registry.code)


main()
