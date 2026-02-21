# Test: @nocopy type passed to protocol method Own[T] param -- non-last-use should error
from typing import Protocol
from tpy import Int32, Own, nocopy

@nocopy
class Handle:
    fd: Int32

class Consumer(Protocol):
    def consume(self, h: Own[Handle]) -> Int32: ...

class MyConsumer:
    def consume(self, h: Own[Handle]) -> Int32:
        return h.fd

def use_consumer(c: MyConsumer) -> None:
    h: Handle = Handle()
    h.fd = 42
    result: Int32 = c.consume(h)  # tpyc: error(/@nocopy.*used after/)
    print(h.fd)

def main() -> None:
    c: MyConsumer = MyConsumer()
    use_consumer(c)

main()
