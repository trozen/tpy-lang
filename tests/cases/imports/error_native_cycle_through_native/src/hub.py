__tpy__ = {"mod": "native"}
from tpy import int32
from tpy.extern import native
import a
import b

@native("hubns::Hub")
class Hub:
    bias: int32

    def __init__(self) -> None: ...
