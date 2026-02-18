# Invalid type arg on module.func[T](args) syntax.
from tpy import Int32, Ptr, Array
import tpy.unsafe as m

def main() -> None:
    arr: Array[Int32, 2] = [Int32(1), Int32(2)]
    p: Ptr[Int32] = m.unsafe_ptr(arr)
    q = m.unsafe_cast[Nope](p)  # tpyc: error(/Unknown type/)

main()
