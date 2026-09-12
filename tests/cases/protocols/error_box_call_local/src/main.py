# Inverse of dyn_box_fresh_local: a local bound from a plain call (not a ctor)
# could hold a subclass, so boxing it is still rejected (slicing guard).
from typing import Protocol
from tpy import dynamic, Own, int32
from tplib import Box


@dynamic
class Conn(Protocol):
    def port(self) -> int32: ...


class HttpConn(Conn):
    _port: int32
    def __init__(self, p: int32) -> None:
        self._port = p
    def port(self) -> int32:
        return self._port


def make() -> Own[HttpConn]:
    return HttpConn(80)


def main() -> None:
    c = make()                # init is a call, not a ctor -> not fresh-ctor
    b: Box[Conn] = Box(c)     # tpyc: error(/dynamic type may be a subclass|slicing/)
    print(b.port())


main()
