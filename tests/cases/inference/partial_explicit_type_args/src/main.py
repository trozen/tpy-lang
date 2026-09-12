# Partial explicit type args: provide some type args, infer the rest.
from tpy import int32, uint32, Ptr
from tpy.unsafe import unsafe_cast, unsafe_ptr, unsafe_load

def main() -> None:
    # unsafe_cast with explicit target type, source inferred from arg
    nums: list[int32] = [int32(1), int32(2), int32(3)]
    p: Ptr[int32] = unsafe_ptr(nums)
    q = unsafe_cast[uint32](p)  # T=uint32 explicit, U=int32 from arg
    print(unsafe_load(q, uint32(0)))
    print(unsafe_load(q, uint32(1)))
    print("done")

main()
