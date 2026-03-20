from tpy import Ptr, Int32, take_ptr

def test() -> None:
    x: Int32 = Int32(42)
    p: Ptr[None] = take_ptr(x)  # tpyc: error(/Type mismatch/)

test()
