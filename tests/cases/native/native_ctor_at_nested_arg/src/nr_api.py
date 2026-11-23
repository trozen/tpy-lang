# The bindings both the bare and the module-qualified calls reach.
from tpy.extern import native
from tpy import int32


@native('mylog::NR')
class NR:
    @native('mylog::NR')
    def __init__(self, x: int32) -> None: ...

    @native('absorb')
    def absorb(self, o: NR) -> int32: ...


@native('mylog::bump_nr')
def bump_nr(r: NR) -> int32: ...


@native('mylog::push_list')
def push_list(xs: list[int32], v: int32) -> int32: ...
