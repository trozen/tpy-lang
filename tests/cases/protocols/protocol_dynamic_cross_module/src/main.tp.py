# Tests cross-module @dynamic protocol: import, local vars, params, return, structural conformance.
from pet import Pet

# Direct inheritance (C++ struct Dog : __tpy_Base_Pet)
class Dog(Pet):
    def speak(self) -> str:
        return "Woof"

# Structural conformance (no explicit inheritance)
class Cat:
    def speak(self) -> str:
        return "Meow"

def greet(pet: Pet) -> None:
    print(pet.speak())

def echo(pet: Pet) -> Pet:
    return pet

def main() -> None:
    # Local variable with direct inheritor
    dog: Pet = Dog()
    print(dog.speak())

    # Local variable with structural conformance
    cat: Pet = Cat()
    print(cat.speak())

    # Pass to function param
    greet(Dog())
    greet(Cat())

    # Return type
    p = echo(dog)
    print(p.speak())

main()
