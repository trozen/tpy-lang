# A sibling module for the module-qualified call sections of main.py.
from typing import Iterator
from tpy import int32, dispatch


@dispatch
def ident[T](o: T) -> T:
    return o


@dispatch
def ident[T](o: T, n: int32) -> T:
    return o


@dispatch
def rep[T](o: T) -> Iterator[T]:
    yield o


@dispatch
def rep[T](o: T, n: int32) -> Iterator[T]:
    for _ in range(n):
        yield o
