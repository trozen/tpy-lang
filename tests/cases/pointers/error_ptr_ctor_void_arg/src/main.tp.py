from tpy import Ptr, Int32

def test() -> None:
    x: Int32 = Int32(42)
    p: Ptr[None] = Ptr[None](x)  # tpyc: error(/does not accept arguments/)

test()
