from tpy import Int32, StaticList, Ptr, noalloc

class Item:
    value: Int32

    def __init__(self, v: Int32 = 0):
        self.value = v

@noalloc
def process_list(items: StaticList[Item, 16]) -> None:
    # append
    items.append(Item(10))
    items.append(Item(20))

    # push_empty returns Ptr (uses default constructor)
    p: Ptr[Item] = items.push_empty()
    p.value = 30

    # subscript access
    first: Item = items[0]
    items[1] = Item(first.value + 5)

@noalloc
def print_list(items: StaticList[Item, 16]) -> None:
    for i in range(len(items)):
        item: Item = items[i]
        print(item.value)

items = StaticList[Item, 16]()
process_list(items)
print(len(items))
print_list(items)

# Test initializer list constructor
nums: StaticList[Int32, 8] = StaticList[Int32, 8]([100, 200, 300])
print(len(nums))
print(nums[0])
print(nums[2])

# Test fill constructor via list repetition
filled: StaticList[Int32, 8] = StaticList[Int32, 8]([0]*8)
filled[0] = 42
print(len(filled))
print(filled[0])
print(filled[7])
