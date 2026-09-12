# Test that list repeat size must match Array size annotation
from tpy import Array, int32

def main() -> None:
    a: Array[int32, 2] = [1, 2, 3] * 1  # tpyc: error(/List repeat produces 3 elements/)

main()
