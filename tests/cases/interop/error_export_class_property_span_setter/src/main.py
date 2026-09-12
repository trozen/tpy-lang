# A Span property setter is rejected: a setter's canonical body stores its
# value, but the buffer copy-in's backing vector dies when the wrapper
# returns, so the stored span would dangle. Methods keep Span params (read
# for the call's duration); setters must take list[T].
# tpy: ext_module
from tpy import int32, Span
from tpy.extern import export


@export
class Buf:
    _total: int32

    def __init__(self) -> None:
        self._total = 0

    @property
    def data(self) -> int32:
        return self._total

    @data.setter
    def data(self, v: Span[int32]) -> None:  # tpyc: error(/property 'data' setter cannot take a Span/)
        total = 0
        for x in v:
            total += x
        self._total = total
