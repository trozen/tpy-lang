# del on array elements is not supported: Array declares no __delitem__
from tpy import int32, Array

def main() -> None:
    a: Array[int32, 3] = [1, 2, 3]
    del a[0]  # tpyc: error(/Type 'Array\[int32, 3\]' does not support item deletion/)

main()
