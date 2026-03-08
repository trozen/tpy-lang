# Test const auto& binding for loop variables that are never mutated
from tpy import Int32, Ptr, copy, readonly

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    @readonly
    def value(self) -> Int32:
        return self.x + self.y

class Container:
    items: list[Int32]
    def __init__(self) -> None:
        self.items = [Int32(1), Int32(2)]

def mutate_point(p: Point) -> None:
    p.x = Int32(0)

def read_point(p: readonly[Point]) -> Int32:
    return p.x

def test_read_only_loop() -> None:
    """Readonly method + field read -> const auto&."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    total: Int32 = Int32(0)
    for p in items:
        total = total + p.value()
    print(total)

def test_non_readonly_method_loop() -> None:
    """Non-readonly method on loop var -> auto&&."""
    items: list[list[Int32]] = [[Int32(1)], [Int32(2)]]
    for lst in items:
        lst.append(Int32(99))
    print(len(items[Int32(0)]))

def test_field_mutate_loop() -> None:
    """Field write on loop var -> auto&&."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    for p in items:
        p.x = Int32(99)
    print(items[Int32(0)].x)

def test_nested_field_mutate_loop() -> None:
    """Nested field write (p.items.append) -> auto&&."""
    items: list[Container] = [Container()]
    for c in items:
        c.items.append(Int32(99))
    print(len(items[Int32(0)].items))

def test_assign_to_local_loop() -> None:
    """Assigning loop var to local (takes address) -> auto&&."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    saved: Point = Point(Int32(0), Int32(0))
    for p in items:
        saved = p
    print(saved.x, saved.y)

def find_point(items: list[Point], target: Int32) -> Point | None:
    """Returning loop var (takes address) -> auto&&."""
    for p in items:
        if p.x == target:
            return p
    return None

def test_pass_to_mutating_func() -> None:
    """Passing loop var to non-readonly param -> auto&&."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    for p in items:
        mutate_point(p)
    print(items[Int32(0)].x)

def test_pass_to_readonly_func() -> None:
    """Passing loop var to readonly param -> const auto&."""
    items: list[Point] = [Point(Int32(7), Int32(8))]
    total: Int32 = Int32(0)
    for p in items:
        total = total + read_point(p)
    print(total)

def test_ptr_from_loop_var() -> None:
    """Taking mutable Ptr to loop var -> auto&&."""
    items: list[Point] = [Point(Int32(3), Int32(4))]
    for p in items:
        ptr: Ptr[Point] = Ptr(p)
        ptr.x = Int32(42)
    print(items[Int32(0)].x)

def test_value_type_loop() -> None:
    """Value type loop var -> typed copy (not const ref)."""
    items: list[Int32] = [Int32(10), Int32(20), Int32(30)]
    total: Int32 = Int32(0)
    for n in items:
        total = total + n
    print(total)

def test_sequential_loops_same_var() -> None:
    """Second loop with same var name (read-only) -> const auto&."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    for p in items:
        p.x = Int32(99)
    total: Int32 = Int32(0)
    for p in items:
        total = total + p.value()
    print(total)

test_read_only_loop()
test_non_readonly_method_loop()
test_field_mutate_loop()
test_nested_field_mutate_loop()
test_assign_to_local_loop()
items_for_find: list[Point] = [Point(Int32(5), Int32(6))]
result = find_point(items_for_find, Int32(5))
if result is not None:
    print(result.x)
test_pass_to_mutating_func()
test_pass_to_readonly_func()
test_ptr_from_loop_var()
test_value_type_loop()
test_sequential_loops_same_var()
