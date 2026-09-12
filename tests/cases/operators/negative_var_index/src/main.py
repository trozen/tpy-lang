# Variable negative index on Array, list, and str
from tpy import int32, Array

def main() -> None:
    arr: Array[int32, 3] = [10, 20, 30]
    i: int32 = -1
    print(arr[i])

    nums: list[int32] = [1, 2, 3, 4, 5]
    j: int32 = -2
    print(nums[j])

    s: str = "hello"
    k: int32 = -3
    print(s[k])

main()
