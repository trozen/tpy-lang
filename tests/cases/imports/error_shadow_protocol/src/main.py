# Protocol base class shadowed by local def -- not recognized as protocol
from typing import Protocol

def Protocol() -> int:
    return 0

class Printable(Protocol):  # tpyc: error(/Protocol/)
    def to_str(self) -> str: ...
