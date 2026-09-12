from tpy import int32


def test_empty_list() -> int:
    items: list[int] = []
    items.append(1)
    items.append(2)
    items.append(3)
    return len(items)


def test_empty_list_int32() -> int32:
    nums: list[int32] = []
    nums.append(int32(10))
    nums.append(int32(20))
    return nums[0] + nums[1]


def test_list_constructor() -> int:
    items: list[int] = list()
    items.append(5)
    items.append(6)
    return len(items)


def test_list_constructor_int32() -> int32:
    nums: list[int32] = list()
    nums.append(int32(100))
    return nums[0]


# Global empty list
global_list: list[int] = []

# Global with list() constructor
global_list2: list[int32] = list()

print(test_empty_list())
print(test_empty_list_int32())
print(test_list_constructor())
print(test_list_constructor_int32())

global_list.append(100)
print(len(global_list))

global_list2.append(int32(50))
print(len(global_list2))
