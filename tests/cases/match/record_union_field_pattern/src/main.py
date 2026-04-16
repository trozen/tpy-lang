# Nested type patterns on union-typed record fields (runtime holds_alternative checks)
from tpy import Int32


class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


class Wrapper:
    pet: Cat | Dog
    def __init__(self, pet: Cat | Dog) -> None:
        self.pet = pet  # tpyc: warning(/copies/)


# Type pattern with as-binding on union field
def describe(w: Wrapper) -> str:
    match w:
        case Wrapper(pet=Cat() as c):
            return "cat: " + c.name
        case Wrapper(pet=Dog() as d):
            return "dog: " + d.name
        case _:
            return "unknown"


# Nested record pattern with field extraction on union field
def get_name(w: Wrapper) -> str:
    match w:
        case Wrapper(pet=Cat(name=n)):
            return "cat " + n
        case Wrapper(pet=Dog(name=n)):
            return "dog " + n
        case _:
            return "unknown"


# Type pattern without binding (condition only)
def is_cat(w: Wrapper) -> str:
    match w:
        case Wrapper(pet=Cat()):
            return "yes"
        case _:
            return "no"


# Mixed: union field type pattern + regular literal on same record
class Tagged:
    tag: str
    value: str | Int32
    def __init__(self, tag: str, value: str | Int32) -> None:
        self.tag = tag
        self.value = value


def show_tagged(t: Tagged) -> str:
    match t:
        case Tagged(tag="s", value=str() as v):
            return "string: " + v
        case Tagged(tag="n", value=Int32() as n):
            return "number: " + str(n)
        case _:
            return "other"


def main() -> None:
    w1 = Wrapper(Cat("Whiskers"))
    w2 = Wrapper(Dog("Rex"))
    print(describe(w1))
    print(describe(w2))
    print(get_name(w1))
    print(get_name(w2))
    print(is_cat(w1))
    print(is_cat(w2))

    t1 = Tagged("s", "hello")
    t2 = Tagged("n", Int32(42))
    t3 = Tagged("x", "other")
    print(show_tagged(t1))
    print(show_tagged(t2))
    print(show_tagged(t3))

main()
