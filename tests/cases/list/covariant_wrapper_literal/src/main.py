# A list literal (and comprehension) whose elements are a covariant-generic
# wrapper upcast to the annotated element type: Box[Dog]/Box[Cat] -> Box[Pet],
# Rc[Dog] -> Rc[Pet]. The element guard exempts covariant generics (a
# representation-preserving converting move, not slicing), matching what the
# .append() path already accepts. Value-record Child->Base stays rejected
# (see list/error_list_assign_subclass).
from typing import Protocol
from tpy import dynamic
from tplib.box import Box
from tplib.rc import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Cat(Pet):
    def name(self) -> str:
        return "cat"


def main() -> None:
    boxes: list[Box[Pet]] = [Box(Dog()), Box(Cat())]   # covariant element upcast
    for b in boxes:
        print(b.get().name())

    shared: list[Rc[Pet]] = [Rc.new(Dog()), Rc.new(Cat())]   # sibling: Rc covariance
    for r in shared:
        print(r.get().name())

    comp: list[Box[Pet]] = [Box(Dog()) for _ in range(2)]   # comprehension form
    print(len(comp))


main()
