from tpy import Ptr, int32, take_ptr

def test() -> None:
    x: int32 = int32(42)
    p: Ptr[None] = take_ptr(x)  # tpyc: error(/Type mismatch/)

test()
