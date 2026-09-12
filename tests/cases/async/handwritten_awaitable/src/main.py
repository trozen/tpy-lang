# Hand-written awaitable using Poll/Waker -- verifies the user-facing
# async types are wired correctly. The class declares conformance to
# `Awaitable[int32]` explicitly (the protocol is structural, so this
# is documentation; the compiler validates the `poll` method signature
# against the protocol).
from tpy import int32
from tpy import Own
from tpy.coro import Poll, Waker, Awaitable, poll_ready, poll_once

class ReadyAwaitable(Awaitable[int32]):
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def __poll__(self, waker: Waker) -> Own[Poll[int32]]:
        return poll_ready(self.value)

def main() -> None:
    a = ReadyAwaitable(int32(123))
    print(poll_once(a).value())

main()
