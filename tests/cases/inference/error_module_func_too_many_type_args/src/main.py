# Too many explicit type args via module.func[T, U, V](args) syntax.
from tpy import int32, uint32, Ptr, Array
import tpy.unsafe as m

def main() -> None:
    arr: Array[int32, 2] = [int32(1), int32(2)]
    p: Ptr[int32] = m.unsafe_ptr(arr)
    q = m.unsafe_cast[int32, uint32, int32](p)  # tpyc: error(/too many|expects 2 type argument/)

main()
