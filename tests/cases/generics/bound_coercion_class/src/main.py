# Bound-based subtype coercion Ptr[U] -> Ptr[Animal] via a class bound U: Animal:
# covers a subclass upcast, the identity case (U == bound), and the readonly form.
from tpy import Ptr, int32, readonly, take_ptr


class Animal:
    code: int32
    def __init__(self, code: int32) -> None:
        self.code = code
    def base_code(self) -> int32:
        return self.code


class Dog(Animal):
    pass


def as_animal[U: Animal](p: Ptr[U]) -> Ptr[Animal]:
    return p  # tpyc: ok


def as_animal_ro[U: Animal](p: Ptr[readonly[U]]) -> Ptr[readonly[Animal]]:
    return p  # tpyc: ok


def main() -> None:
    d = Dog(7)
    pa = as_animal[Dog](take_ptr(d))
    a = Animal(3)
    pa2 = as_animal[Animal](take_ptr(a))
    cdp: Ptr[readonly[Dog]] = take_ptr(d)
    pa_ro = as_animal_ro[Dog](cdp)
    print(pa.base_code(), pa2.base_code(), pa_ro.code)


main()
