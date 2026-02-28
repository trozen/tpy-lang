# Positive out-of-bounds Array access should panic
from tpy import Int32, Array

def main() -> None:
    arr: Array[Int32, 3] = [10, 20, 30]
    print(arr[10])

main()
