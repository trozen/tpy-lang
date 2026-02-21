# Test that list repeat size must match Array size annotation
from tpy import Array, Int32

def main() -> None:
    a: Array[Int32, 2] = [1, 2, 3] * 1  # tpyc: error(/List repeat produces 3 elements/)

main()
