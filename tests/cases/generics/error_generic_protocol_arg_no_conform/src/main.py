# Regression guard: a record passed via a generic outer param to a
# generic-protocol-typed callee must still be rejected when the record
# does not conform to the protocol. The same Ref-stripping fix that
# enables structural inference for conforming records must NOT let
# non-conforming records through.
from tpy.coro import Awaitable, Poll, Waker, poll_once, poll_pending
from tpy import Own


class NotAwaitable[T]:
    # Lacks __poll__, so does not conform to Awaitable[T].
    def whatever(self, w: Waker) -> Own[Poll[T]]:
        return poll_pending()


def drive[T](aw: Awaitable[T]) -> Own[Poll[T]]:
    return poll_once(aw)


def outer[T](t: NotAwaitable[T]) -> None:
    drive(t)  # tpyc: error(/Cannot infer/)
