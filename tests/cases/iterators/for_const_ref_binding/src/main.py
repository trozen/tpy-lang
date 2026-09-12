# Test const auto& binding for loop variables that are never mutated
from tpy import int32, Ptr, copy, readonly

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    @readonly
    def value(self) -> int32:
        return self.x + self.y

class Container:
    items: list[int32]
    def __init__(self) -> None:
        self.items = [int32(1), int32(2)]

def mutate_point(p: Point) -> None:
    p.x = int32(0)

def read_point(p: readonly[Point]) -> int32:
    return p.x

def test_read_only_loop() -> None:
    """Readonly method + field read -> const auto&."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    total: int32 = int32(0)
    for p in items:
        total = total + p.value()
    print(total)

def test_non_readonly_method_loop() -> None:
    """Non-readonly method on loop var -> auto&&."""
    items: list[list[int32]] = [[int32(1)], [int32(2)]]
    for lst in items:
        lst.append(int32(99))
    print(len(items[int32(0)]))

def test_field_mutate_loop() -> None:
    """Field write on loop var -> auto&&."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    for p in items:
        p.x = int32(99)
    print(items[int32(0)].x)

def test_nested_field_mutate_loop() -> None:
    """Nested field write (p.items.append) -> auto&&."""
    items: list[Container] = [Container()]
    for c in items:
        c.items.append(int32(99))
    print(len(items[int32(0)].items))

def test_assign_to_local_loop() -> None:
    """Assigning loop var to local (takes address) -> auto&&."""
    items: list[Point] = [Point(int32(1), int32(2))]
    saved: Point = Point(int32(0), int32(0))
    for p in items:
        saved = p
    print(saved.x, saved.y)

def find_point(items: list[Point], target: int32) -> Point | None:
    """Returning loop var (takes address) -> auto&&."""
    for p in items:
        if p.x == target:
            return p
    return None

def test_pass_to_mutating_func() -> None:
    """Passing loop var to non-readonly param -> auto&&."""
    items: list[Point] = [Point(int32(1), int32(2))]
    for p in items:
        mutate_point(p)
    print(items[int32(0)].x)

def test_pass_to_readonly_func() -> None:
    """Passing loop var to readonly param -> const auto&."""
    items: list[Point] = [Point(int32(7), int32(8))]
    total: int32 = int32(0)
    for p in items:
        total = total + read_point(p)
    print(total)

def test_ptr_from_loop_var() -> None:
    """Taking mutable Ptr to loop var -> auto&&."""
    items: list[Point] = [Point(int32(3), int32(4))]
    for p in items:
        ptr: Ptr[Point] = p
        ptr.x = int32(42)
    print(items[int32(0)].x)

def test_value_type_loop() -> None:
    """Value type loop var -> typed copy (not const ref)."""
    items: list[int32] = [int32(10), int32(20), int32(30)]
    total: int32 = int32(0)
    for n in items:
        total = total + n
    print(total)

def test_sequential_loops_same_var() -> None:
    """Second loop with same var name (read-only) -> const auto&."""
    items: list[Point] = [Point(int32(1), int32(2))]
    for p in items:
        p.x = int32(99)
    total: int32 = int32(0)
    for p in items:
        total = total + p.value()
    print(total)

def test_bigint_const_ref() -> None:
    """Expensive value type (BigInt) unmutated -> const BigInt&."""
    items: list[int] = [10, 20, 30]
    total: int = 0
    for x in items:
        total = total + x
    print(total)

def test_bigint_mutated() -> None:
    """Expensive value type (BigInt) mutated -> BigInt copy."""
    items: list[int] = [10, 20, 30]
    total: int = 0
    for x in items:
        x = x + 1
        total = total + x
    print(total)

test_bigint_const_ref()
test_bigint_mutated()
test_read_only_loop()
test_non_readonly_method_loop()
test_field_mutate_loop()
test_nested_field_mutate_loop()
test_assign_to_local_loop()
items_for_find: list[Point] = [Point(int32(5), int32(6))]
result = find_point(items_for_find, int32(5))
if result is not None:
    print(result.x)
test_pass_to_mutating_func()
test_pass_to_readonly_func()
test_ptr_from_loop_var()
test_value_type_loop()
test_sequential_loops_same_var()
