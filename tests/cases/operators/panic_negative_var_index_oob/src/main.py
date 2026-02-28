# Variable negative index out of bounds on Array
from tpy import Int32, Array

def main() -> None:
    arr: Array[Int32, 3] = [10, 20, 30]
    i: Int32 = -4
    print(arr[i])

main()
