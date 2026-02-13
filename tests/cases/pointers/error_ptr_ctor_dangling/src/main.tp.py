from tpy import Ptr, Int32

def bad_direct() -> Ptr[Int32]:
    x: Int32 = Int32(1)
    return Ptr(x)  # tpyc: error(/returned pointer would dangle/)
