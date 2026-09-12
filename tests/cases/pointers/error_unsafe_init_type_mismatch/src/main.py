# unsafe_init with wrong value type
from tpy import Ptr, int32
from tpy.unsafe import unsafe_alloc, unsafe_init, unsafe_drop, unsafe_free

def main() -> None:
    p: Ptr[int32] = unsafe_alloc()
    unsafe_init(p, "hello")  # tpyc: error(/Cannot infer type arguments/)
    unsafe_drop(p)
    unsafe_free(p)

main()
