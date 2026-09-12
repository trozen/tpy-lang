# Test that consuming methods on types with __del__ suppress the destructor,
# preventing double-free. Without suppression, the moved-from object's dtor
# would run unsafe_free on already-freed memory.
from typing import Self
from tpy import Own, Ptr, int32
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop, unsafe_move_out

class HeapVal:
    _ptr: Ptr[int32]

    def __init__(self, value: int32):
        print("init", value)
        self._ptr = unsafe_alloc()
        unsafe_init(self._ptr, value)

    def __del__(self):
        print("del")
        unsafe_drop(self._ptr)
        unsafe_free(self._ptr)

    def take(self: Own[Self]) -> int32:
        print("take")
        val: int32 = unsafe_move_out(self._ptr)
        unsafe_free(self._ptr)
        return val

def main() -> None:
    h = HeapVal(int32(42))
    val: int32 = h.take()
    print("got", val)

    # Temporary
    val2: int32 = HeapVal(int32(99)).take()
    print("got", val2)

main()
