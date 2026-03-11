# Tests cross-module parameter mutation inference: imported functions have
# resolved mutated_params from their own module's Phase 2.
from tpy import Int32
from helpers import Point, sum_points, add_point, add_point_wrapper, read_wrapper

# --- Non-mutating imported function: no warning ---

def test_imported_read_no_warn() -> None:
    """Imported read-only function: no warning."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    sum_points(items)  # tpyc: ok
    print(v.x)

# --- Mutating imported function: warns ---

def test_imported_mutate_warns() -> None:
    """Imported mutating function: warns."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    add_point(items, Point(Int32(9), Int32(9)))  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

# --- Transitive: imported wrapper that calls mutator ---

def test_imported_transitive_mutation_warns() -> None:
    """Imported wrapper that transitively mutates: warns."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    add_point_wrapper(items, Point(Int32(9), Int32(9)))  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

# --- Transitive: imported wrapper that only reads ---

def test_imported_transitive_read_no_warn() -> None:
    """Imported wrapper that transitively only reads: no warning."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    read_wrapper(items)  # tpyc: ok
    print(v.x)

test_imported_read_no_warn()
test_imported_mutate_warns()
test_imported_transitive_mutation_warns()
test_imported_transitive_read_no_warn()
