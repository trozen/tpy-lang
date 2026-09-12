# Variable negative index out of bounds on Array
from tpy import int32, Array

def main() -> None:
    arr: Array[int32, 3] = [10, 20, 30]
    i: int32 = -4
    print(arr[i])

main()
