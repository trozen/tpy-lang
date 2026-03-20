from tpy import Ptr, Int32, take_ptr

def bad_via_var() -> Ptr[Int32]:
    x: Int32 = Int32(1)
    p: Ptr[Int32] = take_ptr(x)
    return p  # tpyc: error(/returned pointer would dangle/)
