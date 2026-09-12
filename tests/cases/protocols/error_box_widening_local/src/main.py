# Inverse of dyn_box_fresh_local: a widening annotation (`d: Base = Sub()`) makes
# the local's static type wider than the ctor, so its dynamic type (Sub) isn't
# provably its static type and boxing it is still rejected (locks var_type ==
# init_type; boxing would slice the Sub into a Base).
from typing import Protocol
from tpy import dynamic, int32
from tplib import Box


@dynamic
class Conn(Protocol):
    def port(self) -> int32: ...


class Base(Conn):
    _port: int32
    def __init__(self, p: int32) -> None:
        self._port = p
    def port(self) -> int32:
        return self._port


class Sub(Base):
    def port(self) -> int32:
        return self._port + 1


def main() -> None:
    d: Base = Sub(80)          # widening: static Base, dynamic Sub -> not fresh-ctor
    b: Box[Conn] = Box(d)      # tpyc: error(/dynamic type may be a subclass|slicing/)
    print(b.port())


main()
