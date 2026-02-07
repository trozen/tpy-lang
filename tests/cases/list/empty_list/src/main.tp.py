from tpy import Int32


def test_empty_list() -> int:
    items: list[int] = []
    items.append(1)
    items.append(2)
    items.append(3)
    return len(items)


def test_empty_list_int32() -> Int32:
    nums: list[Int32] = []
    nums.append(Int32(10))
    nums.append(Int32(20))
    return nums[0] + nums[1]


def test_list_constructor() -> int:
    items: list[int] = list()
    items.append(5)
    items.append(6)
    return len(items)


def test_list_constructor_int32() -> Int32:
    nums: list[Int32] = list()
    nums.append(Int32(100))
    return nums[0]


# Global empty list
global_list: list[int] = []

# Global with list() constructor
global_list2: list[Int32] = list()

print(test_empty_list())
print(test_empty_list_int32())
print(test_list_constructor())
print(test_list_constructor_int32())

global_list.append(100)
print(len(global_list))

global_list2.append(Int32(50))
print(len(global_list2))
