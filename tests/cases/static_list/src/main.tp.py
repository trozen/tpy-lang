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

    # get and set
    first: Item = items.get(0)
    items.set(1, Item(first.value + 5))

@noalloc
def print_list(items: StaticList[Item, 16]) -> None:
    for i in range(len(items)):
        item: Item = items.get(i)
        print(item.value)

items = StaticList[Item, 16]()
process_list(items)
print(len(items))
print_list(items)
