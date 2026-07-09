# The RwLock read guard yields readonly[T] -- a shared reader must not mutate.
# Mutating through it (here via the Deref chain to list.append) is a compile
# error, the same readonly enforcement Box[readonly[T]] gets. The write guard is
# the mutable path.
from tpy.sync import RwLock


def main() -> None:
    rw = RwLock.new([1, 2])
    with rw.read() as r:
        r.append(3)  # tpyc: error(/non-readonly method 'append' on readonly reference/)


main()
