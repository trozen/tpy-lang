# Invalid type arg on module.func[T](args) syntax.
from tpy import int32, Ptr, Array
import tpy.unsafe as m

def main() -> None:
    arr: Array[int32, 2] = [int32(1), int32(2)]
    p: Ptr[int32] = m.unsafe_ptr(arr)
    q = m.unsafe_cast[Nope](p)  # tpyc: error(/Unknown type/)

main()
