# Base @dynamic protocol defined in a separate module.
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...
