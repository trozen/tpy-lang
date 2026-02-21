# Defines a @dynamic protocol in a separate module for cross-module import testing.
from typing import Protocol
from tpy import dynamic

@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...
