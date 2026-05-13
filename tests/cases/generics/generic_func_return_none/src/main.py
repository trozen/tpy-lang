# Regression: `def f[T] -> T` compiles when T = None (void in C++).
from tpy import Int32
from tpy.coro import Awaitable, Poll, Waker, poll_once, poll_ready, poll_ready_none


class Int32One:
    def __poll__(self, w: Waker) -> Poll[Int32]:
        return poll_ready(Int32(1))


class Nothing:
    def __poll__(self, w: Waker) -> Poll[None]:
        return poll_ready_none()


def drain[T](aw: Awaitable[T]) -> T:
    return poll_once(aw).value()


def main() -> None:
    print(drain(Int32One()))
    drain(Nothing())
    print("done")


main()
