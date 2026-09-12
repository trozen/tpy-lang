# A free-copy-scalar match binding (int32) is copied, so mutating the subject
# storage in the arm (field assign, method call, container realloc) keeps the
# matched value rather than dangling -- matching CPython, no warning.
from tpy import int32


class Dog:
    legs: int32

    def __init__(self, legs: int32) -> None:
        self.legs = legs


class Cat:
    age: int32

    def __init__(self, age: int32) -> None:
        self.age = age


class Holder:
    pet: Dog | Cat

    def __init__(self) -> None:
        self.pet = Dog(4)

    def replace(self) -> None:
        self.pet = Cat(99)


def field_direct(h: Holder) -> None:
    match h.pet:
        case Dog(legs=lg):
            h.pet = Cat(99)  # destroys the Dog in place; lg is a copy
            print(lg)
        case Cat(age=a):
            print("cat", a)


def field_method(h: Holder) -> None:
    match h.pet:
        case Dog(legs=lg):
            h.replace()  # method-call mutation of the subject
            print(lg)
        case Cat(age=a):
            print("cat", a)


def element_realloc(xs: list[Dog | Cat]) -> None:
    match xs[0]:
        case Dog(legs=lg):
            xs.append(Cat(1))  # realloc moves the element; lg is a copy
            print(lg)
        case Cat(age=a):
            print("cat", a)


def main() -> None:
    field_direct(Holder())
    field_method(Holder())
    element_realloc([Dog(4)])


main()
