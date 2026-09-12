# del on array elements is not supported (arrays are fixed-size)
from tpy import int32, Array

def main() -> None:
    a: Array[int32, 3] = [1, 2, 3]
    del a[0]  # tpyc: error(/fixed-size/)

main()
