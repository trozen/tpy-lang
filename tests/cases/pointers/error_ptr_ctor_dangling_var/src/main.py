from tpy import Ptr, int32, take_ptr

def bad_via_var() -> Ptr[int32]:
    x: int32 = int32(1)
    p: Ptr[int32] = take_ptr(x)
    return p  # tpyc: error(/returned pointer would dangle/)
