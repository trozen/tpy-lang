# class Dog(Pet) with @dynamic Pet but Dog missing a required method
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...
    def name(self) -> str:
        ...

class Dog(Pet):  # tpyc: error(/missing required methods: name/)
    def make_noise(self) -> str:
        return "Woof"
