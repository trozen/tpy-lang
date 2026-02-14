from tpy import *

def test() -> None:
    arr: Array[Int32, 2] = [Int32(1), Int32(2)]
    unsafe_ptr(arr)  # tpyc: error(/Unknown function or type/)
