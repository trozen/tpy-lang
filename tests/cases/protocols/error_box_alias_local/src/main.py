# Inverse of dyn_box_fresh_local: an ALIAS of a fresh-ctor local (bound by a
# name, not a ctor call) is not itself a fresh-ctor local, so boxing it is still
# rejected (locks the isinstance-TpyCall check).
from typing import Protocol
from tpy import dynamic, int32
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


def main() -> None:
    d = HttpConn(80)
    e = d                      # alias (name init, not a ctor) -> not fresh-ctor
    b: Box[Conn] = Box(e)      # tpyc: error(/dynamic type may be a subclass|slicing/)
    print(b.port())


main()
