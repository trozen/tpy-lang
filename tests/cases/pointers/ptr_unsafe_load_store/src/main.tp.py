from tpy import Ptr, ConstPtr, Int32

def test_store_and_load() -> None:
    x: Int32 = Int32(5)
    p: Ptr[Int32] = Ptr(x)
    p.unsafe_store(Int32(0), Int32(99))
    val: Int32 = p.unsafe_load(Int32(0))
    print(val)
    print(x)

def test_constptr_load() -> None:
    x: Int32 = Int32(42)
    cp: ConstPtr[Int32] = ConstPtr(x)
    val: Int32 = cp.unsafe_load(Int32(0))
    print(val)

test_store_and_load()
test_constptr_load()
