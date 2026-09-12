from tpy import Ptr, int32, take_ptr

def bad_direct() -> Ptr[int32]:
    x: int32 = int32(1)
    return take_ptr(x)  # tpyc: error(/returned pointer would dangle/)
