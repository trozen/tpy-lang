# Returning readonly[DynamicProto] from functions (const Base& return path)
from tpy import dynamic, readonly
from typing import Protocol

@dynamic
class Pet(Protocol):
    @readonly
    def name(self) -> str: ...

class Dog(Pet):
    @readonly
    def name(self) -> str:
        return "Rex"

class Cat(Pet):
    @readonly
    def name(self) -> str:
        return "Whiskers"

global_pet: Pet = Cat()

def echo_readonly(pet: readonly[Pet]) -> readonly[Pet]:
    return pet

def get_global_readonly() -> readonly[Pet]:
    return global_pet

def main() -> None:
    dog = Dog()
    print(echo_readonly(dog).name())
    print(get_global_readonly().name())

main()
