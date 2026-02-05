from typing import Protocol

class Printable(Protocol):
    def to_string(self) -> str:
        ...
