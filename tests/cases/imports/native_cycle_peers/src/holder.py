# Section regular_native: a regular module whose only cycle peer is native.
__tpy__ = {"includes": ["cycle_types.hpp"]}
from tpy import int32, Ptr
from tpy.extern import native

@native("eng::Msg")
class Msg:
    n: int32

    def __init__(self) -> None: ...

# Late: engine imports Msg back from this module, which must exist first.
from engine import Engine  # tpyc: warning(/Import statement should be at the top/)

class Holder:
    engine: Ptr[Engine]

    # The native peer by value in `__init__`, emitted inline in the header.
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # The native peer by value in a plain method.
    def rebind(self, engine: Engine) -> None:
        self.engine = engine

    def run(self) -> int32:
        return self.engine.take(Msg())

class Garage:
    # The native peer held by value: its header is complete here.
    engine: Engine

    def __init__(self) -> None:
        self.engine = Engine()

    def run(self) -> int32:
        # Bound first: BUGS.md#native-method-temp-arg-ref-param.
        m = Msg()
        return self.engine.take(m)

# A class derived from the native peer class.
class Turbo(Engine):
    def __init__(self) -> None:
        super().__init__()
        self.bias = 2

    def boost(self) -> int32:
        m = Msg()
        return self.take(m) * 2
