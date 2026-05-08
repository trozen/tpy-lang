# Hand-written awaitable using Poll/Waker -- verifies the user-facing
# async types are wired correctly. The class declares conformance to
# `Awaitable[Int32]` explicitly (the protocol is structural, so this
# is documentation; the compiler validates the `poll` method signature
# against the protocol).
#
# Note: tests with statically-resolved awaited types use
# `tpy.coro.poll_once` directly once the protocol-param + explicit-T
# template-arg inference path is fixed (tracked in
# `docs/ASYNC_PROGRESS.md`); hand-written awaitables drive via a
# local cpp_template helper until then.
from tpy.extern import cpp_template
from tpy import Int32
from tpy.coro import Poll, Waker, Awaitable, poll_ready

class ReadyAwaitable(Awaitable[Int32]):
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def poll(self, waker: Waker) -> Poll[Int32]:
        return poll_ready(self.value)

@cpp_template("std::move(({0}).poll(::tpy::Waker{{}})).value()")
def drive(a: ReadyAwaitable) -> Int32: ...

def main() -> None:
    a = ReadyAwaitable(Int32(123))
    print(drive(a))

main()
