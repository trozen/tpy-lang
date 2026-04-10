# Error: protocols cannot be nested inside classes
from typing import Protocol

class Outer:
    class Inner(Protocol):  # tpyc: error(/Protocols cannot be nested/)
        def foo(self) -> int: ...
