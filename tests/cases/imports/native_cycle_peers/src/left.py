# Section through_native: two regular modules in a cycle through a native one.
"""A regular module of the cycle; its docstring runs no code, so the cycle allows it."""
__tpy__ = {"includes": ["cycle_types.hpp"]}
from tpy import int32, Ptr
from tpy.extern import native

@native("hubns::Note")
class Note:
    n: int32

    def __init__(self) -> None: ...

# Late: hub imports Note back from this module, which must exist first.
from hub import Hub  # tpyc: warning(/Import statement should be at the top/)

class Left:
    hub: Ptr[Hub]

    def __init__(self, hub: Ptr[Hub]) -> None:
        self.hub = hub

    def run(self) -> int32:
        return self.hub.read_note(Note())
