# `@dynamic` protocol value -> `Ptr[Protocol]` requires a mutable lvalue
# source (same gate as the existing `record -> Ptr[T]` upcast). A
# constructor-temporary cannot be the source -- the address-take would
# dangle.
from typing import Protocol
from tpy import Ptr, dynamic


@dynamic
class P(Protocol):
    def step(self) -> None: ...


class Impl(P):
    def step(self) -> None:
        pass


def take_ptr(p: Ptr[P]) -> None:
    p.step()


def caller() -> None:
    take_ptr(Impl())  # tpyc: error(/Cannot take mutable pointer to read-only or temporary value/)


caller()
