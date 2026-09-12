from tpy import int32, Ptr

# Test 1: Global int used in range bounds
start = 0
end = 3
for i in range(start, end):
    print(i)

# Test 2: Global int used as list index
items: list[int32] = [10, 20, 30]
idx = 1
print(items[idx])

# Test 3: Global int used in list repeat count
count = 3
repeated: list[int32] = [0] * count
print(len(repeated))

# Test 4: Global int reassignment (z = 0; z = 5 pattern)
z = 0
print(z)
z = 5
print(z)

# Test 5: Membership operator on global list
global_list: list[int32] = [1, 2, 3]
if 2 in global_list:
    print(1)
else:
    print(0)
if 5 in global_list:
    print(1)
else:
    print(0)

# Test 6: Loop variable shadows global
i = 100
print(i)
for i in range(0, 2):
    print(i)

# Test 7: For-each loop variable shadows global
x = 999
print(x)
nums: list[int32] = [7, 8]
for x in nums:
    print(x)

# Test 8: Global pointer field access
class Point:
    a: int32
    b: int32

    def __init__(self, a: int32, b: int32) -> None:
        self.a = a
        self.b = b

local_pt: Point = Point(42, 99)
global_ptr: Ptr[Point] = local_pt
print(global_ptr.a)
print(global_ptr.b)

# Test 9: int32 += with global default-int value
counter: int32 = 10
increment = 5
counter += increment
print(counter)

# Test 10: Global in binop with method template
a = 10
b = 3
print(a + b)
print(a - b)
print(a * b)
print(a // b)

# Test 11: Method parameter shadows global
class Counter:
    val: int32

    def __init__(self, val: int32) -> None:
        # 'val' param shadows global 'val' below - should NOT deref
        self.val = val

    def add(self, a: int32) -> int32:
        # 'a' param shadows global 'a' above - should NOT deref
        return self.val + a

val = 999  # Global that's shadowed by __init__ param
c: Counter = Counter(50)
print(c.val)
print(c.add(7))

# Test 12: Augmented subscript assignment with global default-int RHS
arr: list[int32] = [100, 200, 300]
delta = 5
arr[0] += delta  # global BigInt on RHS needs deref before .to_int32()
print(arr[0])
