__tpy__ = {"mod": "native", "includes": ["cycle_types.hpp"]}
from tpy import int32
from tpy.extern import native
from left import Note
from right import Tag

@native("hubns::Hub")
class Hub:
    bias: int32

    def __init__(self) -> None: ...

    def read_note(self, n: Note) -> int32: ...

    def read_tag(self, t: Tag) -> int32: ...
