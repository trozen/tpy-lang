# A non-readonly METHOD call on the subject (here h.swap() reassigning the
# union field) warns when a non-scalar arm binding borrows the subject -- the
# indirect-mutation case the syntactic stopgap misses. A readonly method does
# not warn. Runtime takes the non-mutating arm.
from tpy import int32, readonly


class Dog:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


class Cat:
    age: int32

    def __init__(self, age: int32) -> None:
        self.age = age


class Holder:
    pet: Dog | Cat

    def __init__(self) -> None:
        self.pet = Cat(7)

    def swap(self) -> None:
        self.pet = Cat(9)

    @readonly
    def peek(self) -> int32:
        return 1


def mutating(h: Holder) -> None:
    match h.pet:
        case Dog(items=lst):
            h.swap()  # tpyc: warning(/'h.pet' may be mutated by 'swap\(\)'/)
            print(len(lst))
        case Cat(age=a):
            print("cat", a)


def readonly_ok(h: Holder) -> None:
    match h.pet:
        case Dog(items=lst):
            n = h.peek()  # readonly -- no warning
            print(len(lst), n)
        case Cat(age=a):
            print("cat-ro", a)


def main() -> None:
    mutating(Holder())
    readonly_ok(Holder())


main()
