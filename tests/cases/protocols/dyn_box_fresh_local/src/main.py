# A freshly-constructed, never-rebound local of a nominal @dynamic root can be
# moved into Box[P] (dynamic type is provably its static type); a field mutation
# before boxing does not disqualify it.
from typing import Protocol
from tpy import dynamic, nocopy, Int32
from tplib import Box


@dynamic
class Conn(Protocol):
    def port(self) -> Int32: ...


@nocopy
class HttpConn(Conn):
    _port: Int32
    def __init__(self, p: Int32) -> None:
        self._port = p
    def port(self) -> Int32:
        return self._port


def main() -> None:
    c = HttpConn(80)          # tpyc: ok
    c._port = 8080            # field mutation -- does not rebind c
    b: Box[Conn] = Box(c)     # tpyc: ok
    print(b.get().port())


main()
