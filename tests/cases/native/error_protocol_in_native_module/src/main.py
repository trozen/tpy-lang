# Test that protocols in native_module produce an error
# tpy: native_module
from typing import Protocol

class Drawable(Protocol):  # tpyc: error(/not allowed in native_module/)
    def draw(self) -> None: ...
