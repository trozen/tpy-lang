# T: Pet still uses static dispatch (template) even when Pet is @dynamic
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...

class Dog(Pet):
    def make_noise(self) -> str:
        return "Woof"

def speak[T: Pet](animal: T) -> None:
    print(animal.make_noise())

def main() -> None:
    d = Dog()
    speak(d)

main()
