# The Uninit* storages' `ptr()` follows its receiver (`@auto_readonly`): a
# write through the pointer of a mutable storage reaches the stored value.
from tpy import int32
from tpy.mem import UninitStorage, UninitArrayStorage, UninitHeapStorage


def poke(s: UninitStorage[int32]) -> None:
    # UninitStorage: the write makes `s` mutable
    p = s.ptr()  # tpyc: ok
    sp = p.span(1)
    sp[0] = 9


def poke_array(a: UninitArrayStorage[int32, 2]) -> None:
    p = a.ptr()  # tpyc: ok
    sp = p.span(1)
    sp[0] = 8


def poke_heap(h: UninitHeapStorage[int32]) -> None:
    p = h.ptr()  # tpyc: ok
    sp = p.span(1)
    sp[0] = 7


def main() -> None:
    s = UninitStorage[int32]()
    s.construct(1)
    poke(s)
    a = UninitArrayStorage[int32, 2]()
    a.init(0, 1)
    poke_array(a)
    h = UninitHeapStorage[int32](1)
    h.init0(1)
    poke_heap(h)
    print("mem:", s.get(), a.load(0), h.load0())
    s.reset()
    a.drop(0)
    h.drop0()
