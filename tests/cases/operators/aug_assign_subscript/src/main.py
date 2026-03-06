from tpy import Int32, Array
from tplib import ArrayList

def test_list_aug_assign() -> None:
    nums: list[Int32] = [1, 2, 3]
    nums[0] += 10
    print(nums[0])  # 11
    nums[1] *= 5
    print(nums[1])  # 10
    nums[2] -= 1
    print(nums[2])  # 2

def test_arraylist_aug_assign() -> None:
    items = ArrayList[Int32, 4]()
    items.append(100)
    items.append(200)
    items[0] += 5
    print(items[0])  # 105
    items[1] -= 50
    print(items[1])  # 150

def test_negative_index_aug_assign() -> None:
    nums: list[Int32] = [10, 20, 30]
    nums[-1] += 5
    print(nums[-1])  # 35
    nums[-2] *= 2
    print(nums[-2])  # 40

def test_array_aug_assign() -> None:
    arr: Array[Int32, 3] = [10, 20, 30]
    arr[0] += 5
    print(arr[0])  # 15
    arr[-1] *= 2
    print(arr[-1])  # 60

test_list_aug_assign()
test_arraylist_aug_assign()
test_negative_index_aug_assign()
test_array_aug_assign()
