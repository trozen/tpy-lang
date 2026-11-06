# A protocol method cannot declare `*args`: a call through the protocol packs
# nothing into it, so it is refused where it is written.
from typing import Protocol
from tpy import int32


class Base(Protocol):
    def f(self, x: int32) -> int32: ...


class ChildArgs(Base, Protocol):
    def f(self, x: int32, *rest: int32) -> int32: ...  # tpyc: error(/variadic parameters are not allowed in protocols \(parameter '\*rest' of method 'f' on protocol 'ChildArgs'\)/)
