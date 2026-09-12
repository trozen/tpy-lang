from tpy import int32, copy

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Test 1: Local sharing — y = x makes both point to same object
def test_local_sharing() -> None:
    x: Point = Point(1, 2)
    y = x
    y.x = 99
    print(x.x)  # 99 — shared
    print(y.x)  # 99

# Test 2: copy() creates independent value
def test_copy_independence() -> None:
    x: Point = Point(10, 20)
    y: Point = copy(x)
    y.x = 999
    print(x.x)  # 10 — independent
    print(y.x)  # 999

# Test 3: Init from param — local points to param's object
def test_init_from_param(p: Point) -> None:
    local = p
    local.x = 42
    print(p.x)  # 42 — shared through param

# Test 4: Init from container element — pointer into container
def test_init_from_element() -> None:
    points: list[Point] = [Point(1, 1), Point(2, 2), Point(3, 3)]
    elem = points[0]
    elem.x = 100
    print(points[0].x)  # 100 — elem pointed into container

# Test 5: For-each over records — loop var is auto& reference
def test_foreach_mutation() -> None:
    points: list[Point] = [Point(1, 10), Point(2, 20), Point(3, 30)]
    for p in points:
        p.x += 1
    print(points[0].x)  # 2
    print(points[1].x)  # 3
    print(points[2].x)  # 4

# Test 6: Rebinding pointer-local to different source
def test_rebinding() -> None:
    a: Point = Point(1, 1)
    b: Point = Point(2, 2)
    x = a
    print(x.x)  # 1
    x = b
    print(x.x)  # 2
    x.x = 77
    print(b.x)  # 77 — x now points to b

# Test 7: Rvalue append — no copy needed
def test_rvalue_append() -> None:
    results: list[Point] = []
    results.append(Point(5, 5))
    results.append(Point(6, 6))
    print(results[0].x)  # 5
    print(results[1].x)  # 6

# Test 8: Build list with copy
def test_build_with_copy() -> None:
    results: list[Point] = []
    p: Point = Point(1, 1)
    p.x = 10
    results.append(copy(p))
    p.x = 20
    results.append(copy(p))  # tpyc: warning(/unnecessary copy/)
    print(results[0].x)  # 10 — independent copy
    print(results[1].x)  # 20 — independent copy

# Test 9: Init from global — local points to global's object
def test_init_from_global() -> None:
    local = g
    local.x = 500
    print(g.x)  # 500 — shared through global

# Test 10: Rebind pointer-local to global
def test_rebind_to_global() -> None:
    a: Point = Point(1, 1)
    x = a
    x = g
    x.x = 600
    print(g.x)  # 600 — x now points to global

# Test 11: List sharing — lists are non-value, assignment shares
def test_list_sharing() -> None:
    a: list[int32] = [1, 2, 3]
    b = a
    b.append(4)
    print(len(a))  # 4 — shared
    print(a[3])    # 4

# Test 12: Pointer copy chain — a→b→c, mutation through c visible in a
def test_pointer_chain() -> None:
    a: Point = Point(1, 1)
    b = a
    c = b
    c.x = 88
    print(a.x)  # 88

# Test 13: User-defined method on pointer-local — uses ->
class Counter:
    val: int32
    def __init__(self, v: int32):
        self.val = v
    def increment(self) -> None:
        self.val = self.val + 1

def test_method_on_pointer_local() -> None:
    c: Counter = Counter(0)
    c.increment()
    c.increment()
    print(c.val)  # 2

# Test 14: For-each value elements from pointer-local list
def test_foreach_value_from_pointer_local() -> None:
    nums: list[int32] = [10, 20, 30]
    total: int32 = 0
    for n in nums:
        total = total + n
    print(total)  # 60

g: Point = Point(0, 0)

test_local_sharing()
test_copy_independence()
pt: Point = Point(0, 0)
test_init_from_param(pt)
test_init_from_element()
test_foreach_mutation()
test_rebinding()
test_rvalue_append()
test_build_with_copy()
test_init_from_global()
test_rebind_to_global()
test_list_sharing()
test_pointer_chain()
test_method_on_pointer_local()
test_foreach_value_from_pointer_local()
