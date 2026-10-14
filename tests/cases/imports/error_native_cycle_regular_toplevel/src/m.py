from tpy import int32
from tpy.extern import native

@native("eng::Msg")
class Msg:
    n: int32

    def __init__(self) -> None: ...

from n import Engine

def compute() -> int32:
    return Engine().take(Msg())

COUNT = compute()  # tpyc: error(/Cyclic import members may only contain imports.*found executable code in 'm'.*Cycle: m <-> n/)
