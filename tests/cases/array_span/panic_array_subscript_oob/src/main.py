# Positive out-of-bounds Array access should panic
from tpy import int32, Array

def main() -> None:
    arr: Array[int32, 3] = [10, 20, 30]
    print(arr[10])

main()
