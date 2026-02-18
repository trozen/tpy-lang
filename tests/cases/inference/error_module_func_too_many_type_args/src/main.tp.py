# Too many explicit type args via module.func[T, U, V](args) syntax.
from tpy import Int32, UInt32, Ptr, Array
import tpy.unsafe as m

def main() -> None:
    arr: Array[Int32, 2] = [Int32(1), Int32(2)]
    p: Ptr[Int32] = m.unsafe_ptr(arr)
    q = m.unsafe_cast[Int32, UInt32, Int32](p)  # tpyc: error(/too many|expects 2 type argument/)

main()
