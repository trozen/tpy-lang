# Test that consuming methods on child classes correctly suppress the inherited
# destructor. The child does not define __del__ itself but the parent does --
# without parent-chain walking this would double-free.
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

    def get(self) -> Int32:
        return self._ptr

class Child(Base):
    def __init__(self, value: Int32):
        super().__init__(value)
        print("Child.init")

    def take(self: Own[Self]) -> Int32:
        print("take")
        val: Int32 = unsafe_move_out(self._ptr)
        unsafe_free(self._ptr)
        return val

def main() -> None:
    c = Child(Int32(42))
    val: Int32 = c.take()
    print("got", val)

    val2: Int32 = Child(Int32(99)).take()
    print("got", val2)

main()
