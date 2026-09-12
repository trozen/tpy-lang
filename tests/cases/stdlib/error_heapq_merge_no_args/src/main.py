# heapq.merge() with no arguments cannot infer its element type T -- the
# documented explicit-type requirement (heapq.merge[T]()).
import heapq
from tpy import int32

def main() -> None:
    out: list[int32] = list(heapq.merge())  # tpyc: error(/Cannot infer type arguments for 'merge'/)
    print(len(out))

main()
