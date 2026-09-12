# A class-bounded generic function infers U from the Ptr[U] argument and
# validates `U: Animal` via is_subclass_of (the satisfies_bound class branch).
from tpy import Ptr, int32, take_ptr


class Animal:
    code: int32
    def __init__(self, code: int32) -> None:
        self.code = code
    def base_code(self) -> int32:
        return self.code


class Dog(Animal):
    pass


def as_animal[U: Animal](p: Ptr[U]) -> Ptr[Animal]:
    return p


def main() -> None:
    d = Dog(11)
    pa = as_animal(take_ptr(d))
    print(pa.base_code())


main()
