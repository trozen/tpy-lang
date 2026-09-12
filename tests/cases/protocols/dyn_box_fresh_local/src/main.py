# A freshly-constructed, never-rebound local of a nominal @dynamic root can be
# moved into Box[P] (dynamic type is provably its static type); a field mutation
# before boxing does not disqualify it.
from typing import Protocol
from tpy import dynamic, nocopy, int32
from tplib import Box


@dynamic
class Conn(Protocol):
    def port(self) -> int32: ...


@nocopy
class HttpConn(Conn):
    _port: int32
    def __init__(self, p: int32) -> None:
        self._port = p
    def port(self) -> int32:
        return self._port


def main() -> None:
    c = HttpConn(80)          # tpyc: ok
    c._port = 8080            # field mutation -- does not rebind c
    b: Box[Conn] = Box(c)     # tpyc: ok
    print(b.get().port())


main()
