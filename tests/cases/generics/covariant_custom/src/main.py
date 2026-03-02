# Custom covariant generic -- verifies converting move constructor generates
# std::move on fields, and correct friend declaration for 2-type-param generic.
from typing import Protocol
from tpy import dynamic, Own, Covariant, Ptr
from tpy.unsafe import unsafe_alloc, unsafe_init, unsafe_drop, unsafe_free

@dynamic
class Animal(Protocol):
    def name(self) -> str: ...

class Dog(Animal):
    _name: str
    def __init__(self, n: str) -> None:
        self._name = n
    def name(self) -> str:
        return self._name

class Cat(Animal):
    _name: str
    def __init__(self, n: str) -> None:
        self._name = n
    def name(self) -> str:
        return self._name

# Two type params: T (covariant), N (invariant).
# Uses Ptr[T] (raw pointer is naturally covariant in C++) and N for a tag.
class Tagged[T, N](Covariant[T]):
    _ptr: Ptr[T]
    _tag: N
    _owned: bool
    def __init__(self, val: Own[T], tag: N) -> None:
        self._ptr = unsafe_alloc[T]()
        unsafe_init(self._ptr, val)
        self._tag = tag
        self._owned = True
    def get(self) -> T:
        return self._ptr
    def tag(self) -> N:
        return self._tag
    def __del__(self) -> None:
        if self._owned:
            unsafe_drop(self._ptr)
            unsafe_free(self._ptr)

def show(t: Tagged[Animal, str]) -> None:
    print(t.tag(), t.get().name())

def make_tagged() -> Own[Tagged[Animal, str]]:
    return Tagged(Dog("Rex"), "pet")

def main() -> None:
    # Function arg coercion: Tagged[Dog, str] -> Tagged[Animal, str]
    td = Tagged(Dog("Buddy"), "good")
    show(td)

    tc = Tagged(Cat("Whiskers"), "lazy")
    show(tc)

    # Variable assignment coercion
    td2 = Tagged(Dog("Max"), "brave")
    animal_tagged: Tagged[Animal, str] = td2
    print(animal_tagged.tag(), animal_tagged.get().name())

    # Return coercion via Own
    t3 = make_tagged()
    print(t3.tag(), t3.get().name())

main()
