# @dynamic protocol variable declared or reassigned inside branches and loops
from tpy import dynamic, Int32
from typing import Protocol

@dynamic
class Pet(Protocol):
    def name(self) -> str:
        ...

class Dog(Pet):
    def name(self) -> str:
        return "Rex"

class Cat:
    def name(self) -> str:
        return "Whiskers"

class Parrot:
    def name(self) -> str:
        return "Polly"

def branch_init(cond: bool) -> None:
    if cond:
        pet: Pet = Dog()
    else:
        pet = Cat()
    print(pet.name())

def branch_reassign(cond: bool) -> None:
    pet: Pet = Dog()
    if cond:
        pet = Cat()
    print(pet.name())

def nested_branches(a: bool, b: bool) -> None:
    pet: Pet = Dog()
    if a:
        if b:
            pet = Cat()
        else:
            pet = Parrot()
    print(pet.name())

def loop_reassign(n: Int32) -> None:
    pet: Pet = Dog()
    i: Int32 = 0
    while i < n:
        pet = Cat()
        i = i + 1
    print(pet.name())

def branch_in_loop(n: Int32) -> None:
    pet: Pet = Dog()
    i: Int32 = 0
    while i < n:
        if i == 1:
            pet = Cat()
        else:
            pet = Parrot()
        i = i + 1
    print(pet.name())

def main() -> None:
    branch_init(True)
    branch_init(False)
    branch_reassign(True)
    branch_reassign(False)
    nested_branches(True, True)
    nested_branches(True, False)
    nested_branches(False, True)
    loop_reassign(0)
    loop_reassign(3)
    branch_in_loop(1)
    branch_in_loop(2)
    branch_in_loop(3)

main()
