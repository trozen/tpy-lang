# Rc[T] works for value-type T (heap-allocated, shared via Rc handle).
# Read-side aliasing across clones; mutation requires the cell's value
# field to be mutable (not exposed in this test since int32 has no
# mutating method).
from tpy import int32
from tplib import Rc


def main() -> None:
    r1 = Rc.new(int32(7))  # tpyc: type(Rc[int32])
    r2 = r1.clone()
    print(r1.get(), r2.get())  # 7 7


main()
