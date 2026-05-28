from __future__ import annotations
# A bounded factory `make[U: T]` is callable by inference: U from the arg,
# T from the LHS hint, U:T bound validated against the substituted form.
from typing import Protocol
from tpy import dynamic, Ptr, Own
from tpy.unsafe import unsafe_take, unsafe_release


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Dog(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return "dog:" + self.label


class PetBox[T]:
    _payload: Ptr[T]

    def __init__(self, payload: Ptr[T]) -> None:
        self._payload = payload

    def __del__(self) -> None:
        unsafe_release(self._payload)

    def get(self) -> T:
        return self._payload

    @staticmethod
    def make[U: T](value: Own[U]) -> Own[PetBox[T]]:
        return PetBox[T](unsafe_take(value))


def main() -> None:
    a: PetBox[Pet] = PetBox.make(Parrot("Polly"))
    b: PetBox[Pet] = PetBox.make(Dog("Rex"))
    print(a.get().name())
    print(b.get().name())


main()
