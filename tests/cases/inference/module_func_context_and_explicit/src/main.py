# Module.func with context-only inference vs explicit type args.
from tpy import int32, uint32, Ptr, Array
import tpy.unsafe as m

def main() -> None:
    arr: Array[int32, 2] = [int32(1), int32(2)]
    p: Ptr[int32] = m.unsafe_ptr(arr)

    # Context-only: T=uint32 inferred from assignment target
    q1: Ptr[uint32] = m.unsafe_cast(p)
    print(m.unsafe_load(q1, uint32(0)))

    # Explicit type arg: T=uint32, U=int32 inferred from arg
    q2: Ptr[uint32] = m.unsafe_cast[uint32](p)
    print(m.unsafe_load(q2, uint32(0)))

    print("done")

main()
