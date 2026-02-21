# Partial explicit type args: provide some type args, infer the rest.
from tpy import Int32, UInt32, Ptr
from tpy.unsafe import unsafe_cast, unsafe_ptr, unsafe_load

def main() -> None:
    # unsafe_cast with explicit target type, source inferred from arg
    nums: list[Int32] = [Int32(1), Int32(2), Int32(3)]
    p: Ptr[Int32] = unsafe_ptr(nums)
    q = unsafe_cast[UInt32](p)  # T=UInt32 explicit, U=Int32 from arg
    print(unsafe_load(q, UInt32(0)))
    print(unsafe_load(q, UInt32(1)))
    print("done")

main()
