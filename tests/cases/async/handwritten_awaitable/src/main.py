# Hand-written awaitable using Poll/Waker -- verifies the user-facing
# async types are wired correctly. The class declares conformance to
# `Awaitable[Int32]` explicitly (the protocol is structural, so this
# is documentation; the compiler validates the `poll` method signature
# against the protocol).
from tpy import Int32
from tpy.coro import Poll, Waker, Awaitable, poll_ready, poll_once

class ReadyAwaitable(Awaitable[Int32]):
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def __poll__(self, waker: Waker) -> Poll[Int32]:
        return poll_ready(self.value)

def main() -> None:
    a = ReadyAwaitable(Int32(123))
    print(poll_once(a).value())

main()
