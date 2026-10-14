# Test that protocols in a native module ("mod": "native") produce an error
__tpy__ = {"mod": "native"}
from typing import Protocol

class Drawable(Protocol):  # tpyc: error(/not allowed in a native module/)
    def draw(self) -> None: ...
