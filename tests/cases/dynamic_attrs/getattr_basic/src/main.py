# D16 Phase 1: __getattr__ -> Any routes undeclared attribute reads through the dunder;
# also dynamic / plain attribute writes whose receiver runs once.
from tpy import int32
from typing import Any, cast
from tplib.box import Box

class Config:
    _data: dict[str, Any]

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

def main() -> None:
    cfg = Config({"host": "localhost", "port": int32(8080)})
    host = cast(str, cfg.host)
    port = cast(int32, cfg.port)
    print(host)
    print(port)

class Echo:
    def __setattr__(self, name: str, value: int) -> None:
        print("set", name, value)

    def __delattr__(self, name: str) -> None:
        print("del", name)

def consumed_receiver() -> None:
    # A dunder fallback's receiver is evaluated once: consuming it there is
    # one consume.
    e = Box(Echo())
    setattr(e.take(), "a", 6)  # tpyc: ok
    e = Box(Echo())
    delattr(e.take(), "b")  # tpyc: ok
    e = Box(Echo())
    del e.take().c  # tpyc: ok

class Point:
    v: int32

    def __init__(self) -> None:
        self.v = 0

class Holder:
    p: Point
    calls: int32

    def __init__(self) -> None:
        self.p = Point()
        self.calls = 0

    def get(self) -> Point:
        self.calls += 1
        return self.p

def counted_receiver() -> None:
    # A plain field write's receiver is analyzed ahead of the target (for the
    # setattr-only check) and evaluated once.
    h = Holder()
    h.get().v = 1  # tpyc: ok
    print("plain write:", h.calls, h.p.v)

main()
consumed_receiver()
counted_receiver()
