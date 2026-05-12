# Rc[T] works for value-type T (heap-allocated, shared via Rc handle).
# Read-side aliasing across clones; mutation requires the cell's value
# field to be mutable (not exposed in this test since Int32 has no
# mutating method).
from tpy import Int32
from tplib import Rc, make_rc


def main() -> None:
    r1 = make_rc(Int32(7))
    r2 = r1.clone()
    print(r1.get(), r2.get())  # 7 7


main()
