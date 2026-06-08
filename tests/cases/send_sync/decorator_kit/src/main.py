# Send/Sync opt-in / opt-out kit: class Foo(Send) verified opt-in,
# @unsafe_send forcing a Ptr-holding record Send, @nosend/@nosync opt-out
# on a structurally-Send record, and function-level overrides steering the
# FrameType answer (@unsafe_send on a borrowing async def, @nosend making
# a free function unusable as Send[Callable]).
import asyncio
from tpy import Int32, Ptr, Send, unsafe_send, unsafe_sync, nosend, nosync, take_ptr
from typing import Iterator

class Trade(Send):
    sym: Int32
    qty: Int32

    def __init__(self, sym: Int32, qty: Int32) -> None:
        self.sym = sym
        self.qty = qty

@unsafe_send
class NativeHandle:
    raw: Ptr[Int32]

    def __init__(self, raw: Ptr[Int32]) -> None:
        self.raw = raw

@nosend
@nosync
class ArenaBuffer:
    data: list[Int32]

    def __init__(self) -> None:
        self.data = []

@unsafe_sync
class SharedTable:
    data: list[Int32]

    def __init__(self) -> None:
        self.data = []

@unsafe_send
async def forced(xs: list[Int32]) -> Int32:    # tpyc: frame_send(yes)
    return len(xs)

@nosync
def gen_forced(n: Int32) -> Iterator[Int32]:    # tpyc: frame_send(yes) frame_sync(no)
    i = 0
    while i < n:
        yield i
        i += 1

def main() -> None:
    t = Trade(1, 2)         # tpyc: is_send(yes) is_sync(yes)
    h = NativeHandle(take_ptr(t.sym))  # tpyc: is_send(yes)
    a = ArenaBuffer()       # tpyc: is_send(no) is_sync(no)
    s = SharedTable()       # tpyc: is_send(yes) is_sync(yes)
    print(t.qty, len(a.data), len(s.data))
    xs = [1, 2, 3]
    print(asyncio.run(forced(xs)))
    print(list(gen_forced(3)))

main()
