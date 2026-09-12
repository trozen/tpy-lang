# unsafe_store rejects value type that doesn't match pointer element type
from tpy import Ptr, int32, uint32, take_ptr
from tpy.unsafe import unsafe_store

def main() -> None:
    x: int32 = int32(5)
    p: Ptr[int32] = take_ptr(x)
    unsafe_store(p, uint32(0), "hello")  # tpyc: error(/Cannot infer type arguments/)

main()
