# Warn on list mutation during iteration (borrow conflict)
from tpy import int32

def test_append() -> None:
    items: list[int32] = [int32(1), int32(2), int32(3)]
    for x in items:
        items.append(x)  # tpyc: warning(/Mutation of 'items'.*'append'/)

def test_pop() -> None:
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        items.pop()  # tpyc: warning(/Mutation of 'items'.*'pop'/)

def test_insert() -> None:
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        items.insert(int32(0), int32(9))  # tpyc: warning(/Mutation of 'items'.*'insert'/)

def test_remove() -> None:
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        items.remove(int32(1))  # tpyc: warning(/Mutation of 'items'.*'remove'/)

def test_clear() -> None:
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        items.clear()  # tpyc: warning(/Mutation of 'items'.*'clear'/)

def test_extend() -> None:
    items: list[int32] = [int32(1), int32(2)]
    other: list[int32] = [int32(3)]
    for x in items:
        items.extend(other)  # tpyc: warning(/Mutation of 'items'.*'extend'/)

def test_reverse() -> None:
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        items.reverse()  # tpyc: warning(/Mutation of 'items'.*'reverse'/)

def test_sort() -> None:
    items: list[int32] = [int32(3), int32(1)]
    for x in items:
        items.sort()  # tpyc: warning(/Mutation of 'items'.*'sort'/)

def test_del() -> None:
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        del items[int32(0)]  # tpyc: warning(/Mutation of 'items'.*'del'/)

def test_nested_loops() -> None:
    outer: list[int32] = [int32(1)]
    inner: list[int32] = [int32(2)]
    for x in outer:
        for y in inner:
            inner.append(y)  # tpyc: warning(/Mutation of 'inner'/)
        outer.append(x)  # tpyc: warning(/Mutation of 'outer'/)

def test_conditional_mutation() -> None:
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        if x > int32(0):
            items.append(x)  # tpyc: warning(/Mutation of 'items'/)

def test_subscript_assign_ok() -> None:
    """Element replacement doesn't invalidate iterators."""
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        items[int32(0)] = int32(9)  # tpyc: ok

def test_no_warn_after_loop() -> None:
    """Mutation after loop exit is fine."""
    items: list[int32] = [int32(1), int32(2)]
    for x in items:
        pass
    items.append(int32(3))  # tpyc: ok

def test_read_only_ok() -> None:
    """No warnings for read-only operations during iteration."""
    items: list[int32] = [int32(1), int32(2), int32(3)]
    total: int32 = int32(0)
    for x in items:
        total += x        # tpyc: ok
        _ = len(items)    # tpyc: ok
        _ = items[int32(0)]  # tpyc: ok

def test_outer_loan_survives_inner_while() -> None:
    """A loop shape that takes no iterator loan must not expire the outer one."""
    items: list[int32] = [1, 2]
    i: int32 = 0
    for x in items:
        while i < 2:
            i += 1
        items.append(x)  # tpyc: warning(/Mutation of 'items'.*'append'/)

def test_else_clause_ok() -> None:
    """The `else` clause runs after the iterator is done -- mutating there is fine."""
    items: list[int32] = [1, 2]
    total: int32 = 0
    for x in items:
        total += x
    else:
        items.append(3)  # tpyc: ok
    print("else_clause:", total, len(items))

def main() -> None:
    test_else_clause_ok()

main()
