"""Which state machine a resumable frame is.

A leaf module: the THIR frame and the codegen printer both name the shape,
and importing it from either package would load that package first.
"""
from enum import Enum


class ResumableShape(Enum):
    """Which state machine a resumable frame is.

    ASYNC -- `async def`: `await` -> `__poll__(Waker) -> Poll<T>`.
    GENERATOR -- generator: `yield` -> `__next__() -> expected<T,
        StopIteration>`.
    """
    ASYNC = "async"
    GENERATOR = "generator"
