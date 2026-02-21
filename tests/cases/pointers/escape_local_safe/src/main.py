from tpy import Int32, copy

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# copy() suppresses escape error
def loop_escape_copy_ok() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        p: Point = Point(i, i)
        saved = copy(p)  # tpyc: ok
    print(saved.x, saved.y)

# Rvalue in loop: no escape (fresh storage)
def loop_rvalue_ok() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        saved = Point(i, i)  # tpyc: ok
    print(saved.x, saved.y)

# For-each from outer-scoped container: safe
def foreach_outer_container() -> None:
    items: list[Point] = [Point(1, 2), Point(3, 4)]
    saved: Point = Point(0, 0)
    for p in items:
        saved = p  # tpyc: ok
    print(saved.x, saved.y)

# Value type: no escape concern
def value_type_ok() -> None:
    saved: Int32 = 0
    for i in range(3):
        n: Int32 = i * 10
        saved = n  # tpyc: ok
    print(saved)

# For-each var name reused: p first declared inside a loop, then reused
# as for-each var over an outer container. Depth should be container's (1),
# not the stale depth (2) from the previous loop.
def foreach_shadow_safe() -> None:
    items: list[Point] = [Point(7, 8)]
    saved: Point = Point(0, 0)
    for i in range(1):
        p: Point = Point(i, i)
        pass
    for p in items:
        saved = p  # tpyc: ok
    print(saved.x, saved.y)

# Param name reused as for-each var: param depth should be restored
# after the loop, not stuck at the for-each depth.
def param_reused_as_loop_var(p: Point) -> None:
    items: list[Point] = [Point(5, 6)]
    saved: Point = Point(0, 0)
    for p in items:
        saved = p  # tpyc: ok
    # After loop, p's depth is restored to param depth (1).
    # Assigning param to saved is safe (same depth).
    saved = p  # tpyc: ok
    print(saved.x, saved.y)

# Sequential loops with same var name: first loop's depth must not
# leak into second loop.
def sequential_loops_same_var() -> None:
    items1: list[Point] = [Point(10, 20)]
    items2: list[Point] = [Point(30, 40)]
    saved: Point = Point(0, 0)
    for p in items1:
        pass
    for p in items2:
        saved = p  # tpyc: ok
    print(saved.x, saved.y)

# Same-scope assignment: both vars at same depth, no escape.
def same_scope_ok() -> None:
    a: Point = Point(1, 1)
    b: Point = a  # tpyc: ok
    print(b.x)

# Lvalue-init pointer-local with rvalue rebind in loop: rebind slot
# is pre-declared at function scope so it outlives the loop.
def lvalue_init_rvalue_rebind() -> None:
    items: list[Point] = [Point(1, 2), Point(3, 4), Point(5, 6)]
    best: Point = items[0]
    for p in items:
        if p.x > best.x:
            best = copy(p)  # tpyc: ok
    print(best.x, best.y)

# Rvalue-init pointer-local: alias preserves original value after rebind.
# Uses separate init/rebind slots so alias still sees the old object.
def rvalue_alias_preserved() -> None:
    p: Point = Point(1, 1)
    alias: Point = p  # tpyc: ok
    p = Point(2, 2)
    print(alias.x, alias.y)
    print(p.x, p.y)

# Rvalue rebind in one if-branch only.
def if_branch_rvalue_rebind() -> None:
    p: Point = Point(1, 1)
    if p.x > 0:
        p = Point(2, 2)
    print(p.x, p.y)

# Different rvalue rebinds in if vs else.
def if_else_rvalue_rebinds() -> None:
    p: Point = Point(1, 1)
    if p.x > 0:
        p = Point(2, 2)
    else:
        p = Point(3, 3)
    print(p.x, p.y)

# Alias preserved through if-branch rvalue rebind.
def if_alias_preserved() -> None:
    p: Point = Point(1, 1)
    alias: Point = p  # tpyc: ok
    if p.x > 0:
        p = Point(2, 2)
    print(alias.x, alias.y)
    print(p.x, p.y)

# Rvalue rebind inside while loop.
def while_rvalue_rebind() -> None:
    p: Point = Point(0, 0)
    i: Int32 = 0
    while i < 3:
        p = Point(i, i)
        i = i + 1
    print(p.x, p.y)

loop_escape_copy_ok()
loop_rvalue_ok()
foreach_outer_container()
value_type_ok()
foreach_shadow_safe()
# param_reused_as_loop_var: compile-only test (CPython scoping divergence)
sequential_loops_same_var()
same_scope_ok()
lvalue_init_rvalue_rebind()
rvalue_alias_preserved()
if_branch_rvalue_rebind()
if_else_rvalue_rebinds()
if_alias_preserved()
while_rvalue_rebind()
