# Test that consuming methods suppress destructors when both parent and child
# have __del__. The __tpy_owned_ flag must be shared (not shadowed) so that
# both destructors in the chain are skipped on the moved-from object.
from typing import Self
from tpy import Own, Ptr, Int32
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop, unsafe_move_out

class Base:
    _ptr: Ptr[Int32]

    def __init__(self, value: Int32):
        print("Base.init", value)
        self._ptr = unsafe_alloc()
        unsafe_init(self._ptr, value)

    def __del__(self):
        print("Base.del")
        unsafe_drop(self._ptr)
        unsafe_free(self._ptr)

class Child(Base):
    _extra: Int32

    def __init__(self, value: Int32, extra: Int32):
        super().__init__(value)
        self._extra = extra
        print("Child.init", extra)

    def __del__(self):
        print("Child.del")

    def take(self: Own[Self]) -> Int32:
        print("take")
        val: Int32 = unsafe_move_out(self._ptr)
        unsafe_free(self._ptr)
        return val

def main() -> None:
    c = Child(Int32(42), Int32(7))
    val: Int32 = c.take()
    print("got", val)

main()
