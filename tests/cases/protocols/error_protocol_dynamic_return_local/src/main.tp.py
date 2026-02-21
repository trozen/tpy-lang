# Returning a locally-constructed @dynamic protocol value is rejected (would dangle)
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def name(self) -> str:
        ...

class Dog(Pet):
    def name(self) -> str:
        return "Rex"

def make_pet() -> Pet:
    return Dog()  # tpyc: error(/Cannot return local or temporary as 'Pet'/)
