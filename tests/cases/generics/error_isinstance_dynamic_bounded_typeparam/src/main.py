# isinstance on a type parameter bounded by a @dynamic protocol is rejected:
# the dynamic_cast lowering for type-param subjects is not yet supported.
from typing import Protocol
from tpy import dynamic

@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...

class Dog(Pet):
    def __init__(self):
        pass
    def speak(self) -> str:
        return "woof"

def f[T: Pet](x: T) -> bool:
    return isinstance(x, Dog)  # tpyc: error(/bounded by the polymorphic/)

def main():
    print(f(Dog()))

main()
