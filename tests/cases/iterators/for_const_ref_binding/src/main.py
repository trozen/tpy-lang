# Test const auto& binding for loop variables that are never mutated, and the
# mutation credit that climbs from a loop var back to the storage it came out
# of -- through a name, a field, or a SUBSCRIPT of either.
#
# `test_chain_source_loops` covers the widened for-each source: any rooted
# lvalue CHAIN (field and subscript hops off a declared name) iterates exactly
# as a bare name does, in every position. Every section mutates through the
# loop var and reads the change back out of the caller's container, so a
# silent copy shows up as a stale value.
#
# `alias_read` / `alias_write` cover the ALIAS leg of the same credit: a
# non-value local bound off the loop var takes a mutable borrow only when it
# is written through, and the read-only leg must bind `const T&` or the C++
# reference discards qualifiers.
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

class Grid:
    rows: list[list[list[Point]]]
    def __init__(self) -> None:
        self.rows = [[[Point(1, 2)]], [[Point(3, 4)]]]

    def bump_own(self) -> None:
        """METHOD position: the subscript hop roots at `self`, so the method
        must not be inferred readonly."""
        for row in self.rows:
            for p in row[0]:  # tpyc: ok
                p.x += 5

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

def test_nested_loop_mutate() -> None:
    """Inner loop var mutated -> the OUTER loop var binds auto&& too.

    The element the inner loop lends comes out of the outer loop var's
    binding, so a const outer binding makes the write ill-formed.
    """
    grid: list[list[Point]] = [[Point(1, 2)], [Point(3, 4)]]
    for row in grid:
        for p in row:
            p.x += 5
    print("nested mutate", grid[0][0].x, grid[1][0].x)

def test_nested_loop_read() -> None:
    """Read-only inner body -> BOTH bindings stay const auto&."""
    grid: list[list[Point]] = [[Point(1, 2)], [Point(3, 4)]]
    total = 0
    for row in grid:
        for p in row:
            total = total + p.value()
    print("nested read", total)

def bump_field_nested(g: Grid) -> None:
    """The inner iterable is a SUBSCRIPT of the outer loop var: the element it
    lends still comes out of `g`, so the write credits the parameter (`Grid&`).
    """
    for row in g.rows:
        for p in row[0]:  # tpyc: ok
            p.x += 5

def bump_param_subscript(rows: list[list[Point]], i: int32) -> None:
    """Single loop straight off a subscript of the parameter."""
    for p in rows[i]:  # tpyc: ok
        p.x += 5

def read_param_subscript(rows: readonly[list[list[Point]]], i: int32) -> int32:
    """Read-only body over a readonly root -- the binding STAYS const."""
    total = 0
    for p in rows[i]:  # tpyc: ok
        total = total + p.value()
    return total

def bump_three_levels(cube: list[list[list[list[Point]]]]) -> None:
    """Three nested loops: the credit composes outwards across the hop."""
    for plane in cube:
        for row in plane[0]:
            for p in row:  # tpyc: ok
                p.x += 5

def bump_dict_subscript(table: dict[int32, list[Point]], k: int32) -> None:
    """A dict subscript is the same element borrow as a list subscript."""
    for p in table[k]:  # tpyc: ok
        p.x += 5

def bump_slice_subscript(items: list[Point]) -> None:
    """A SLICE source shares its elements with the sliced container, as in
    CPython, so the write through it reaches the caller's points."""
    for p in items[0:2]:  # tpyc: ok
        p.x += 5

def first_of_row(rows: list[list[Point]]) -> Point | None:
    """Returning the element borrows the param's storage through the subscript.
    """
    for p in rows[0]:  # tpyc: ok
        return p
    return None

def test_subscript_source_loops() -> None:
    """A loop var iterated out of a SUBSCRIPT roots at the subscripted storage.

    Every section mutates through the loop var and reads the change back out of
    the caller's container, so a silent copy would show as a stale value.
    """
    g = Grid()
    bump_field_nested(g)
    g.bump_own()
    print("sub nested", g.rows[0][0][0].x, g.rows[1][0][0].x)

    rows: list[list[Point]] = [[Point(1, 2), Point(3, 4)]]
    bump_param_subscript(rows, 0)
    print("sub param", rows[0][0].x, read_param_subscript(rows, 0))

    cube: list[list[list[list[Point]]]] = [[[[Point(1, 2)]]]]
    bump_three_levels(cube)
    print("sub three", cube[0][0][0][0].x)

    table: dict[int32, list[Point]] = {}
    table[7] = [Point(1, 2)]
    bump_dict_subscript(table, 7)
    print("sub dict", table[7][0].x)

    items: list[Point] = [Point(1, 2), Point(3, 4)]
    bump_slice_subscript(items)
    print("sub slice", items[0].x, items[1].x)

    found = first_of_row(rows)
    if found is not None:
        found.x += 100
    print("sub return", rows[0][0].x)

class Shelf:
    rows: list[list[Point]]
    flat: list[Point]
    def __init__(self) -> None:
        self.rows = [[Point(1, 2)], [Point(3, 4)]]
        self.flat = [Point(5, 6)]

    def bump_row(self, i: int32) -> None:
        """METHOD position: one field hop off `self`, then a subscript."""
        for p in self.rows[i]:  # tpyc: ok
            p.x += 5

class Depot:
    shelf: Shelf
    def __init__(self) -> None:
        self.shelf = Shelf()

    def bump_deep(self, i: int32) -> None:
        """A chain too deep to iterate directly, bound to a local first.

        `for p in self.shelf.rows[i]` rejects -- the loan has no key
        (BUGS.md#iter-borrow-place-needs-hops) -- and binding the
        intermediate record is the workaround: `sh` aliases `self.shelf`,
        so the writes land in `self`.
        """
        sh = self.shelf
        for p in sh.rows[i]:  # tpyc: ok
            p.x += 5

    def bump_flat(self) -> None:
        """The same workaround for a nested FIELD chain with no subscript."""
        sh = self.shelf
        for p in sh.flat:  # tpyc: ok
            p.x += 5

class Tally:
    total: int32
    def __init__(self, d: Depot) -> None:
        """CONSTRUCTOR position."""
        self.total = 0
        sh = d.shelf
        for p in sh.rows[0]:  # tpyc: ok
            self.total = self.total + p.x

def bump_param_field_sub(s: Shelf, i: int32) -> None:
    """FREE FUNCTION: a field of the parameter, then a subscript."""
    for p in s.rows[i]:  # tpyc: ok
        p.x += 5

def bump_elem_field(shelves: list[Shelf]) -> None:
    """A list ELEMENT as the field receiver, bound to a local first."""
    sh = shelves[0]
    for p in sh.flat:  # tpyc: ok
        p.x += 5

def bump_dict_elem_field(table: dict[int32, Shelf], k: int32) -> None:
    """Same, with a dict element as the field receiver."""
    sh = table[k]
    for p in sh.flat:  # tpyc: ok
        p.x += 5

def bump_loop_var_root(shelves: list[Shelf]) -> None:
    """The chain is rooted at the outer LOOP VAR, not at a parameter name."""
    for s in shelves:
        for p in s.rows[0]:  # tpyc: ok
            p.x += 5

def alias_read(ds: list[Depot]) -> int32:
    """A non-value local bound off a const LOOP VAR takes a CONST borrow.

    The read-only body leaves the param const, so the loop var binds const
    too, and an alias out of it must be spelled `const Shelf&` -- a mutable
    one is ill-formed C++, not a stale read. The bare-name and chain-hop
    sources answer with the same verdict.
    """
    total = 0
    for d in ds:
        e = d  # tpyc: ok
        sh = d.shelf  # tpyc: ok
        total = total + e.shelf.flat[0].x + sh.rows[0][0].x
    return total


def alias_write(ds: list[Depot]) -> None:
    """The write leg: the alias is written THROUGH, so the borrow out of the
    loop var is mutable and the caller's depot sees the change."""
    for d in ds:
        sh = d.shelf  # tpyc: ok
        sh.flat[0].x += 5


def bump_ptr_root(g: Ptr[Shelf]) -> None:
    """A `Ptr[T]` param as the chain ROOT: the hop goes through the deref, the
    elements stay mutable, and the write reaches the caller's shelf."""
    for p in g.rows[0]:  # tpyc: ok
        p.x += 5


def read_ptr_root(g: Ptr[Shelf]) -> int32:
    """The read-only twin of the same `Ptr[T]` root."""
    total = 0
    for p in g.rows[0]:  # tpyc: ok
        total = total + p.value()
    return total


def read_ro_chain(s: readonly[Shelf]) -> int32:
    """A readonly root keeps the chain const across the hop.

    Spelled one hop off the readonly PARAM rather than through a local: a
    `readonly[T]` container local is its own reject (`decl.slot_type`), so
    the deep chain has no readonly workaround.
    """
    total = 0
    for p in s.rows[0]:  # tpyc: ok
        total = total + p.value()
    return total

def bump_in_try(s: Shelf) -> int32:
    """TRY/FINALLY body."""
    n = 0
    try:
        for p in s.rows[0]:  # tpyc: ok
            p.x += 5
            n = n + 1
    finally:
        n = n + 100
    return n

def bump_in_match(s: Shelf, k: int32) -> None:
    """MATCH arm body."""
    match k:
        case 0:
            for p in s.rows[0]:  # tpyc: ok
                p.x += 5
        case _:
            pass

def bump_in_closure(s: Shelf) -> None:
    """CLOSURE body -- the chain root is the captured parameter."""
    def inner() -> None:
        for p in s.rows[0]:  # tpyc: ok
            p.x += 5
    inner()

def test_chain_source_loops() -> None:
    """A one-hop chain iterates exactly as a bare name does; a deeper one
    binds to a local first (BUGS.md#iter-borrow-place-needs-hops)."""
    s = Shelf()
    s.bump_row(0)
    bump_param_field_sub(s, 1)
    print("chain method", s.rows[0][0].x, "param", s.rows[1][0].x)

    d = Depot()
    d.bump_deep(0)
    d.bump_flat()
    print("chain deep", d.shelf.rows[0][0].x, "flat", d.shelf.flat[0].x)
    print("chain ctor", Tally(d).total, "readonly", read_ro_chain(d.shelf))

    shelves: list[Shelf] = [Shelf(), Shelf()]
    bump_elem_field(shelves)
    bump_loop_var_root(shelves)
    print("chain elem field", shelves[0].flat[0].x,
          "loop var", shelves[0].rows[0][0].x, shelves[1].rows[0][0].x)

    table: dict[int32, Shelf] = {}
    table[7] = Shelf()
    bump_dict_elem_field(table, 7)
    print("chain dict elem field", table[7].flat[0].x)

    ds: list[Depot] = [Depot()]
    alias_write(ds)
    print("chain alias", alias_read(ds), ds[0].shelf.flat[0].x)

    s3 = Shelf()
    bump_ptr_root(s3)
    print("chain ptr root", s3.rows[0][0].x, read_ptr_root(s3))

    s2 = Shelf()
    print("chain try", bump_in_try(s2), s2.rows[0][0].x)
    bump_in_match(s2, 0)
    bump_in_closure(s2)
    print("chain match+closure", s2.rows[0][0].x)

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
test_nested_loop_mutate()
test_nested_loop_read()
test_subscript_source_loops()
test_chain_source_loops()
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
# MODULE-LEVEL position, chain rooted at a module global.
depot_global = Depot()
shelf_global = depot_global.shelf
for gp in shelf_global.rows[0]:  # tpyc: ok
    gp.x += 5
print("chain module level", depot_global.shelf.rows[0][0].x)
