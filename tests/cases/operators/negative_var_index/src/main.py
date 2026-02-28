# Variable negative index on Array, list, and str
from tpy import Int32, Array

def main() -> None:
    arr: Array[Int32, 3] = [10, 20, 30]
    i: Int32 = -1
    print(arr[i])

    nums: list[Int32] = [1, 2, 3, 4, 5]
    j: Int32 = -2
    print(nums[j])

    s: str = "hello"
    k: Int32 = -3
    print(s[k])

main()
