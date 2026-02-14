from tpy import ConstPtr, Int32

def test() -> None:
    x: Int32 = Int32(5)
    cp: ConstPtr[Int32] = ConstPtr(x)
    cp.unsafe_store(Int32(0), Int32(99))  # tpyc: error(/Cannot call method 'unsafe_store'/)
