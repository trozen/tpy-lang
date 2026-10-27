# Defining a method that hides a parent method emits a warning at the child
# class definition. Static dispatch means parent-typed references will call
# the parent's method, not the child's -- differs from Python. The call site
# warns at a base parameter, a Ptr[Base] one included, and a child
# implementing a @dynamic protocol its base does not still dispatches
# statically through the base.
from typing import Protocol
from tpy import Ptr, dynamic

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def speak(self) -> str:
        return "..."

class Dog(Animal):  # tpyc: warning(/hides 'Animal.speak'/)
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed
    def speak(self) -> str:
        return "Woof!"

def describe(a: Animal) -> str:
    return a.speak()

def describe_ptr(a: Ptr[Animal]) -> str:
    if a is not None:
        return a.speak()
    return ""

@dynamic
class Speaker(Protocol):
    def speak(self) -> str: ...

class Parrot(Animal, Speaker):  # tpyc: warning(/'Parrot.speak' hides 'Animal.speak'/)
    def __init__(self, name: str) -> None:
        super().__init__(name)
    def speak(self) -> str:
        return "Hello!"

def main() -> None:
    d: Dog = Dog("Rex", "Lab")
    # The call site warns too: through 'a' the override is lost (static
    # dispatch runs Animal.speak, not Dog.speak -- divergence from CPython).
    print(describe(d))  # tpyc: warning(/calls through 'a' run 'Animal.speak', not 'Dog.speak'/)
    # The pointer parameter dispatches statically too.
    print(describe_ptr(d))  # tpyc: warning(/'Dog' passed as 'Animal': calls through 'a'/)
    # Speaker is Parrot's protocol, not Animal's: through 'a' the call is static.
    p = Parrot("Polly")
    print(describe(p))  # tpyc: warning(/calls through 'a' run 'Animal.speak', not 'Parrot.speak'/)

main()
