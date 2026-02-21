# Module.func with context-only inference vs explicit type args.
from tpy import Int32, UInt32, Ptr, Array
import tpy.unsafe as m

def main() -> None:
    arr: Array[Int32, 2] = [Int32(1), Int32(2)]
    p: Ptr[Int32] = m.unsafe_ptr(arr)

    # Context-only: T=UInt32 inferred from assignment target
    q1: Ptr[UInt32] = m.unsafe_cast(p)
    print(m.unsafe_load(q1, UInt32(0)))

    # Explicit type arg: T=UInt32, U=Int32 inferred from arg
    q2: Ptr[UInt32] = m.unsafe_cast[UInt32](p)
    print(m.unsafe_load(q2, UInt32(0)))

    print("done")

main()
