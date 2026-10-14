__tpy__ = {"mod": "native"}
from tpy import int32
from tpy.extern import native
from m import Msg

@native("eng::Engine")
class Engine:
    def __init__(self) -> None: ...

    def take(self, m: Msg) -> int32: ...
