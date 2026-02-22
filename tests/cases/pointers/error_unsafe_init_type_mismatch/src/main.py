# unsafe_init with wrong value type
from tpy import Ptr, Int32
from tpy.unsafe import unsafe_alloc, unsafe_init, unsafe_drop, unsafe_free

def main() -> None:
    p: Ptr[Int32] = unsafe_alloc()
    unsafe_init(p, "hello")  # tpyc: error(/No matching overload/)
    unsafe_drop(p)
    unsafe_free(p)

main()
