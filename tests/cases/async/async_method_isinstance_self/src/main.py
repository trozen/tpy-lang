# isinstance(self, Sub) inside an async method body on a polymorphic class.
# Async methods run after construction completes, so the dynamic type of
# self is the real runtime type (unlike __init__/__del__). The async frame
# captures `__self: T&` (or `const T&`); polymorphic_cast_arg returns
# `&__self` so the cast input is correctly `T*`.
from typing import Protocol
from tpy import dynamic, readonly
import asyncio


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    _name: str

    def __init__(self, n: str) -> None:
        self._name = n

    @readonly
    def name(self) -> str:
        return self._name

    @readonly
    async def describe(self) -> str:
        # @readonly async: __self captured as `const T&`; cast emits
        # `const Sub*`; narrowing through __self_ptr binds `const Sub&`.
        await asyncio.sleep(0)
        if isinstance(self, Dog):  # tpyc: ok
            return "dog: " + self.bark()
        return "pet: " + self._name


class Dog(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)

    @readonly
    def bark(self) -> str:
        return "woof from " + self._name


async def describe_via_pet(p: Pet) -> str:
    # Borrow source: isinstance(self, Sub) sees the real runtime type
    # (no slicing because Pet is polymorphic and we accept it by reference).
    return await p.describe()


async def amain() -> None:
    d = Dog("rex")
    p = Pet("plain")
    print(await describe_via_pet(d))
    print(await describe_via_pet(p))


asyncio.run(amain())
