from tpy import Ptr, Int32, take_ptr

def bad_direct() -> Ptr[Int32]:
    x: Int32 = Int32(1)
    return take_ptr(x)  # tpyc: error(/returned pointer would dangle/)
