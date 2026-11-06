# A protocol method cannot declare `**kwargs`: a call through the protocol
# packs nothing into it, so it is refused where it is written.
from typing import Protocol, TypedDict, Unpack
from tpy import int32


class Extras(TypedDict, total=False):
    z: int32


class Base(Protocol):
    def f(self, x: int32) -> int32: ...


class ChildKwargs(Base, Protocol):
    def f(self, x: int32, **rest: Unpack[Extras]) -> int32: ...  # tpyc: error(/variadic parameters are not allowed in protocols \(parameter '\*\*rest' of method 'f' on protocol 'ChildKwargs'\)/)
