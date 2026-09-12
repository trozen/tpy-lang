from tpy import *

def test() -> None:
    arr: Array[int32, 2] = [int32(1), int32(2)]
    unsafe_ptr(arr)  # tpyc: error(/Unknown function or type/)
