# @dynamic protocol type rejected as container element type
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def name(self) -> str: ...

class Dog(Pet):
    def name(self) -> str: return "Rex"

def main() -> None:
    pets: list[Pet] = []  # tpyc: error(/cannot be used as a container element/)

main()
