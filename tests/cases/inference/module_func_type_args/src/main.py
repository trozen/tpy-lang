# Explicit type args on module.func[T](args) call syntax.
from tpy import Int32, UInt32, Ptr, Array
import tpy.unsafe
import tpy.unsafe as m

def main() -> None:
    arr: Array[Int32, 2] = [Int32(1), Int32(2)]
    p: Ptr[Int32] = tpy.unsafe.unsafe_ptr(arr)

    # Dotted module path: tpy.unsafe.func[T](args)
    q1: Ptr[UInt32] = tpy.unsafe.unsafe_cast[UInt32](p)
    print(tpy.unsafe.unsafe_load(q1, UInt32(0)))

    # Aliased module: m.func[T](args)
    q2: Ptr[UInt32] = m.unsafe_cast[UInt32](p)
    print(m.unsafe_load(q2, UInt32(0)))

    print("done")

main()
