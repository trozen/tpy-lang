# Tests parameter mutation inference (8a): no false positives for non-mutating callees.
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

# --- Leaf functions with known mutation behavior ---

def sum_points(items: list[Point]) -> Int32:
    """Reads only -- mutated_params = {}."""
    total: Int32 = 0
    for p in items:
        total += p.x
    return total

def first_x(items: list[Point]) -> Int32:
    """Reads via subscript -- mutated_params = {}."""
    return items[Int32(0)].x

def add_point(items: list[Point], p: Point) -> None:
    """Mutates via append -- mutated_params = {0}."""
    items.append(p)

def replace_first(items: list[Point], p: Point) -> None:
    """Mutates via subscript write -- mutated_params = {0}, structural_mutated_params = {}.
    Subscript write does not reallocate, so element pointers do not dangle."""
    items[Int32(0)] = p

def remove_first(items: list[Point]) -> None:
    """Mutates via del -- mutated_params = {0}."""
    del items[Int32(0)]

def read_point(p: Point) -> Int32:
    """Reads field only -- mutated_params = {}."""
    return p.x

def mutate_point(p: Point, val: Int32) -> None:
    """Mutates via field write -- mutated_params = {0}."""
    p.x = val

# --- Test: non-mutating callee does NOT trigger warning ---

def test_non_mutating_no_warn() -> None:
    """Passing borrowed container to read-only function: no warning."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    sum_points(items)  # tpyc: ok
    print(v.x)

def test_non_mutating_subscript_read() -> None:
    """Passing borrowed container to function that only reads via subscript."""
    items: list[Point] = [Point(Int32(3), Int32(4))]
    v = items[Int32(0)]
    first_x(items)  # tpyc: ok
    print(v.x)

# --- Test: mutating callee DOES trigger warning ---

def test_mutating_append_warns() -> None:
    """Passing borrowed container to function that appends: warns."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    add_point(items, Point(Int32(9), Int32(9)))  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

def test_mutating_subscript_write_no_warn() -> None:
    """Passing borrowed container to function that writes via subscript: no warning.
    Subscript write doesn't reallocate, so element borrows remain valid."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    replace_first(items, Point(Int32(9), Int32(9)))  # tpyc: ok
    print(items[Int32(0)].x)

def test_mutating_del_warns() -> None:
    """Passing borrowed container to function that deletes: warns."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    v = items[Int32(0)]
    remove_first(items)  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

# --- Test: second param not mutated ---

def test_second_param_not_mutated() -> None:
    """add_point mutates param 0 but not param 1; borrowed as param 1 is safe."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    others: list[Point] = [Point(Int32(5), Int32(6))]
    v = others[Int32(0)]
    add_point(items, v)  # tpyc: ok
    print(v.x)

# --- Test: no borrow active = no warn regardless ---

def test_no_borrow_no_warn() -> None:
    """No active borrow, mutating callee: no warning."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    add_point(items, Point(Int32(9), Int32(9)))  # tpyc: ok
    print(len(items))

# --- Test: loop variable mutation inference ---

def test_loop_var_non_mutating_callee() -> None:
    """Non-mutating callee in for-loop body: no iteration mutation warning."""
    items: list[Int32] = [1, 2, 3]
    for x in items:
        sum_points([])  # tpyc: ok
        print(x)

def test_loop_var_mutating_callee() -> None:
    """Mutating callee in for-loop body: warns about iteration mutation."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    for p in items:
        add_point(items, Point(Int32(9), Int32(9)))  # tpyc: warning(/Passing borrowed container 'items'/)
        break

# --- Test: transitive mutation (Phase 2 graph propagation) ---

def add_point_wrapper(items: list[Point], p: Point) -> None:
    """Transitively mutates via add_point -- mutated_params = {0} after Phase 2."""
    add_point(items, p)

def read_wrapper(items: list[Point]) -> Int32:
    """Transitively reads via sum_points -- mutated_params = {} after Phase 2."""
    return sum_points(items)

def test_transitive_mutation_warns() -> None:
    """Wrapper that transitively mutates: warns."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    add_point_wrapper(items, Point(Int32(9), Int32(9)))  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

def test_transitive_read_no_warn() -> None:
    """Wrapper that transitively only reads: no warning."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    read_wrapper(items)  # tpyc: ok
    print(v.x)

# --- Test: forward call (callee defined after caller) ---

def test_forward_mutation_warns() -> None:
    """Forward call to mutating function: warns (deferred to Phase 2)."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    forward_mutator(items)  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

def forward_mutator(items: list[Point]) -> None:
    """Defined after caller -- mutation still detected by Phase 2."""
    items.append(Point(Int32(7), Int32(8)))

def test_forward_read_no_warn() -> None:
    """Forward call to non-mutating function: no warning."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    forward_reader(items)  # tpyc: ok
    print(v.x)

def forward_reader(items: list[Point]) -> Int32:
    """Defined after caller -- non-mutation detected by Phase 2."""
    return sum_points(items)

# --- Test: mutual recursion (cycle fixpoint) ---

def cycle_a(items: list[Point], p: Point) -> None:
    """Mutates via append; calls cycle_b."""
    if len(items) < Int32(5):
        items.append(p)
        cycle_b(items, p)

def cycle_b(items: list[Point], p: Point) -> None:
    """Transitively mutates via cycle_a (mutual recursion)."""
    cycle_a(items, p)

def test_cycle_mutation_warns() -> None:
    """Mutual recursion: cycle_b transitively mutates via cycle_a."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    cycle_b(items, Point(Int32(9), Int32(9)))  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

# --- Test: multi-hop transitive chain (A -> B -> C) ---

def deep_wrapper(items: list[Point], p: Point) -> None:
    """Calls add_point_wrapper which calls add_point -- two hops."""
    add_point_wrapper(items, p)

def test_multi_hop_mutation_warns() -> None:
    """Three-function chain: deep_wrapper -> add_point_wrapper -> add_point."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    deep_wrapper(items, Point(Int32(9), Int32(9)))  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

test_non_mutating_no_warn()
test_non_mutating_subscript_read()
test_mutating_append_warns()
test_mutating_subscript_write_no_warn()
test_mutating_del_warns()
test_second_param_not_mutated()
test_no_borrow_no_warn()
test_loop_var_non_mutating_callee()
test_loop_var_mutating_callee()
test_transitive_mutation_warns()
test_transitive_read_no_warn()
test_forward_mutation_warns()
test_forward_read_no_warn()
test_cycle_mutation_warns()
test_multi_hop_mutation_warns()
