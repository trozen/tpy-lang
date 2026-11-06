# Static dispatch through a base parameter: the lost-override warning at a
# function, method and constructor argument; @dynamic overrides dispatch.
from typing import Protocol, override
from tpy import int32, dynamic


@dynamic
class Speaker(Protocol):
    def speak(self) -> str: ...


class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."


class Dog(Animal):  # tpyc: warning(/hides 'Animal.speak'/)
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def speak(self) -> str:
        return "woof"


class Quiet(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def nap(self) -> str:
        return "zz"


class Pet(Speaker):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."


class Cat(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def speak(self) -> str:
        return "meow"


# Overriding a dict method is a hide: a dict reference runs dict's.
class Counter(dict[str, int32]):  # tpyc: warning(/'Counter.__getitem__' hides 'dict.__getitem__'/)
    def __init__(self) -> None:
        pass

    def __getitem__(self, k: str) -> int32:
        return 0


def animal_name(a: Animal) -> str:
    return a.name


def pet_name(p: Pet) -> str:
    return p.name


def dict_size(m: dict[str, int32]) -> int32:
    return len(m)


# static dispatch through a base param: warned only where an override is lost
def dispatch() -> None:
    print("dispatch", animal_name(Dog("rex")))  # tpyc: warning(/'Dog' passed as 'Animal': calls through 'a' run 'Animal.speak', not 'Dog.speak'/)
    print("dispatch", animal_name(Quiet("q")))  # tpyc: ok
    print("dispatch", pet_name(Cat("tom")))  # tpyc: ok
    c = Counter()
    print("dispatch", dict_size(c))  # tpyc: warning(/'Counter' passed as 'dict\[str, int32\]': calls through 'm' run 'dict.__getitem__', not 'Counter.__getitem__'/)


class Shelter:
    name: str

    def __init__(self, a: Animal) -> None:
        self.name = a.name

    def admit(self, a: Animal) -> str:
        return a.name


# the lost-override warning at a method argument and a constructor argument
def dispatch_sites() -> None:
    d = Dog("rex")
    s = Shelter(d)  # tpyc: warning(/'Dog' passed as 'Animal': calls through parameter 'a' of Shelter.__init__ run 'Animal.speak'/)
    print("dispatch-sites", s.name, s.admit(d))  # tpyc: warning(/'Dog' passed as 'Animal'/)


@dynamic
class Meter(Protocol):
    def m(self) -> int32: ...


class SpA(Meter):
    def m(self) -> int32:
        return 1


class SpB(SpA):
    @override
    def m(self) -> int32:  # tpyc: ok
        return 2


def via_spa(a: SpA) -> int32:
    return a.m()


# @override of an inherited @dynamic protocol member dispatches at run time
# and does not warn
def dyn_override() -> None:
    print("dyn-override", via_spa(SpB()))  # tpyc: ok


def main() -> None:
    dispatch()
    dispatch_sites()
    dyn_override()
