# unsafe_store rejects value type that doesn't match pointer element type
from tpy import Ptr, Int32, UInt32
from tpy.unsafe import unsafe_store

def main() -> None:
    x: Int32 = Int32(5)
    p: Ptr[Int32] = Ptr(x)
    unsafe_store(p, UInt32(0), "hello")  # tpyc: error(/Cannot infer type arguments/)

main()
