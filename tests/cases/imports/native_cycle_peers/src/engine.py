__tpy__ = {"mod": "native", "includes": ["cycle_types.hpp"]}
from tpy import int32
from tpy.extern import native
from holder import Msg

@native("eng::Engine")
class Engine:
    bias: int32

    def __init__(self) -> None: ...

    def take(self, m: Msg) -> int32: ...
