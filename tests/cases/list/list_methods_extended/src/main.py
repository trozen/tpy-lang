from tpy import Int32, StaticList, Ptr

# Test extended list methods: pop(index), index(), count(), reverse(), copy(), __setitem__
# Also test StaticList methods: extend, insert, remove, pop, clear, index, count, reverse, get_mut

def print_list(nums: list[Int32]) -> None:
    """Helper to print list contents."""
    i: Int32 = 0
    while i < len(nums):
        print(nums[i])
        i += 1
    print("---")

def print_staticlist(nums: StaticList[Int32, 16]) -> None:
    """Helper to print StaticList contents."""
    i: Int32 = 0
    while i < len(nums):
        print(nums[i])
        i += 1
    print("---")

# === list[T] methods ===

def test_pop_at_index() -> None:
    """Test pop(index) - remove and return element at index."""
    nums: list[Int32] = [10, 20, 30, 40, 50]

    # Pop from middle
    val: Int32 = nums.pop(2)
    print(val)
    print_list(nums)

    # Pop from beginning
    val = nums.pop(0)
    print(val)
    print_list(nums)

    # Pop with negative index (-1 = last)
    val = nums.pop(-1)
    print(val)
    print_list(nums)

def test_index() -> None:
    """Test index(value) - find index of first occurrence."""
    nums: list[Int32] = [10, 20, 30, 20, 40]

    print(nums.index(10))  # 0
    print(nums.index(20))  # 1 (first occurrence)
    print(nums.index(30))  # 2
    print(nums.index(40))  # 4

def test_count() -> None:
    """Test count(value) - count occurrences."""
    nums: list[Int32] = [1, 2, 2, 3, 2, 4, 2]

    print(nums.count(1))  # 1
    print(nums.count(2))  # 4
    print(nums.count(3))  # 1
    print(nums.count(5))  # 0 (not found)

def test_reverse() -> None:
    """Test reverse() - reverse in place."""
    nums: list[Int32] = [1, 2, 3, 4, 5]
    nums.reverse()
    print_list(nums)

    # Reverse again
    nums.reverse()
    print_list(nums)

def test_copy() -> None:
    """Test copy() - shallow copy."""
    nums: list[Int32] = [1, 2, 3]
    copy: list[Int32] = nums.copy()

    # Modify original
    nums.append(4)

    # Copy should be unaffected
    print(len(nums))   # 4
    print(len(copy))   # 3
    print_list(copy)

def test_setitem() -> None:
    """Test __setitem__ - set element at index."""
    nums: list[Int32] = [10, 20, 30]

    nums[0] = 100
    nums[2] = 300
    print_list(nums)

    # Negative index
    nums[-1] = 999
    print_list(nums)

# === StaticList[T, N] methods ===

def test_staticlist_extend() -> None:
    """Test StaticList.extend()."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(1)
    sl.append(2)

    sl.extend([3, 4, 5])
    print_staticlist(sl)

    # Extend from another list
    more: list[Int32] = [6, 7]
    sl.extend(more)
    print_staticlist(sl)

def test_staticlist_insert() -> None:
    """Test StaticList.insert()."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(10)
    sl.append(30)

    # Insert at beginning
    sl.insert(0, 5)
    print_staticlist(sl)

    # Insert in middle
    sl.insert(2, 20)
    print_staticlist(sl)

def test_staticlist_insert_no_default_ctor() -> None:
    """Test StaticList.insert() with record that has no default constructor."""
    items: StaticList[Item, 16] = StaticList[Item, 16]()
    items.append(Item(10))
    items.append(Item(30))

    # Insert in middle - this verifies T{} is not required
    items.insert(1, Item(20))
    print_item_list(items)

    # Insert at beginning
    items.insert(0, Item(5))
    print_item_list(items)

def test_staticlist_remove() -> None:
    """Test StaticList.remove()."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(10)
    sl.append(20)
    sl.append(30)
    sl.append(20)

    sl.remove(20)
    print_staticlist(sl)

def test_staticlist_pop_at() -> None:
    """Test StaticList.pop(index)."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(10)
    sl.append(20)
    sl.append(30)
    sl.append(40)

    val: Int32 = sl.pop(1)
    print(val)
    print_staticlist(sl)

def test_staticlist_index() -> None:
    """Test StaticList.index()."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(10)
    sl.append(20)
    sl.append(30)

    print(sl.index(10))  # 0
    print(sl.index(20))  # 1
    print(sl.index(30))  # 2

def test_staticlist_count() -> None:
    """Test StaticList.count()."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(1)
    sl.append(2)
    sl.append(2)
    sl.append(3)
    sl.append(2)

    print(sl.count(1))  # 1
    print(sl.count(2))  # 3
    print(sl.count(5))  # 0

def test_staticlist_reverse() -> None:
    """Test StaticList.reverse()."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(1)
    sl.append(2)
    sl.append(3)
    sl.append(4)

    sl.reverse()
    print_staticlist(sl)

def test_staticlist_pop() -> None:
    """Test StaticList.pop() - remove and return last element."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(10)
    sl.append(20)
    sl.append(30)

    val: Int32 = sl.pop()
    print(val)  # 30
    print(len(sl))  # 2

    val = sl.pop()
    print(val)  # 20
    print(len(sl))  # 1

def test_staticlist_clear() -> None:
    """Test StaticList.clear() - remove all elements."""
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(1)
    sl.append(2)
    sl.append(3)
    print(len(sl))  # 3

    sl.clear()
    print(len(sl))  # 0

    # Can append after clear
    sl.append(100)
    print(len(sl))  # 1
    print(sl[0])    # 100

class Item:
    value: Int32

    def __init__(self, v: Int32):
        self.value = v

def print_item_list(items: StaticList[Item, 16]) -> None:
    """Helper to print StaticList[Item] contents."""
    i: Int32 = 0
    while i < len(items):
        print(items[i].value)
        i += 1
    print("---")

def test_staticlist_get_mut() -> None:
    """Test StaticList.get_mut() - get mutable pointer to element."""
    items: StaticList[Item, 16] = StaticList[Item, 16]()
    items.append(Item(10))
    items.append(Item(20))
    items.append(Item(30))

    # Modify via pointer
    p: Ptr[Item] = items.get_mut(1)
    p.value = 200

    print_item_list(items)

# Run all tests
print("=== list pop(index) ===")
test_pop_at_index()
print("=== list index ===")
test_index()
print("=== list count ===")
test_count()
print("=== list reverse ===")
test_reverse()
print("=== list copy ===")
test_copy()
print("=== list setitem ===")
test_setitem()
print("=== StaticList extend ===")
test_staticlist_extend()
print("=== StaticList insert ===")
test_staticlist_insert()
print("=== StaticList insert (no default ctor) ===")
test_staticlist_insert_no_default_ctor()
print("=== StaticList remove ===")
test_staticlist_remove()
print("=== StaticList pop(index) ===")
test_staticlist_pop_at()
print("=== StaticList index ===")
test_staticlist_index()
print("=== StaticList count ===")
test_staticlist_count()
print("=== StaticList reverse ===")
test_staticlist_reverse()
print("=== StaticList pop ===")
test_staticlist_pop()
print("=== StaticList clear ===")
test_staticlist_clear()
print("=== StaticList get_mut ===")
test_staticlist_get_mut()
