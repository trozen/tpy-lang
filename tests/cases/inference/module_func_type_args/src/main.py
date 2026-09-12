# Explicit type args on module.func[T](args) call syntax.
from tpy import int32, uint32, Ptr, Array
import tpy.unsafe
import tpy.unsafe as m

def main() -> None:
    arr: Array[int32, 2] = [int32(1), int32(2)]
    p: Ptr[int32] = tpy.unsafe.unsafe_ptr(arr)

    # Dotted module path: tpy.unsafe.func[T](args)
    q1: Ptr[uint32] = tpy.unsafe.unsafe_cast[uint32](p)
    print(tpy.unsafe.unsafe_load(q1, uint32(0)))

    # Aliased module: m.func[T](args)
    q2: Ptr[uint32] = m.unsafe_cast[uint32](p)
    print(m.unsafe_load(q2, uint32(0)))

    print("done")

main()
