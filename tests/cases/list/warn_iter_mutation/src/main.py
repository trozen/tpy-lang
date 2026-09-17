# Warn on list mutation during iteration (borrow conflict).
# When the loop iterates an ELEMENT (`for v in rows[i]`), the two indices
# decide what the warning may claim. Three verdicts:
#   distinct -- both int/str literals, different values: valid Python, silent;
#   same     -- the identical literal or the identical name: the mutation IS
#               of the iterated element, worded as certain;
#   unknown  -- one literal against a name, two different names, a negative
#               literal, an expression: still warns, but worded "may hit".
# A negative literal is never distinct: rows[-1] is rows[0] in a one-element
# list.
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

class Grid:
    rows: list[list[int32]]

    def __init__(self) -> None:
        self.rows = [[1, 2]]

    # iterating an ELEMENT: a reallocating method on any element of the same
    # container clobbers what the loop points into
    def bump(self, i: int32) -> None:
        for v in self.rows[i]:
            self.rows[i].append(v)  # tpyc: warning(/Mutation of 'self.rows\[\.\.\.\]'.*'append'/)

def test_element_source_method_mutation(rows: list[list[int32]], i: int32) -> None:
    """Iterating rows[i] and appending to rows[i] invalidates the iteration."""
    for v in rows[i]:
        rows[i].append(v)  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]' while iterating over it.*'append' invalidates the iterator/)

def test_element_source_sibling_index(rows: list[list[int32]], i: int32,
                                      j: int32) -> None:
    """Two names may hold the same index: a possible hit, worded as one."""
    for v in rows[i]:
        rows[j].append(v)  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]' may hit the element being iterated.*'append' may invalidate the iterator/)

def test_element_source_distinct_literals(rows: list[list[int32]]) -> None:
    """Valid Python: two int literals that differ cannot name one element."""
    for v in rows[0]:
        rows[1].append(v)  # tpyc: ok

def test_element_source_same_literal(rows: list[list[int32]]) -> None:
    """The same literal twice is the everyday bug the warning exists for."""
    for v in rows[0]:
        rows[0].append(v)  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]' while iterating over it.*'append' invalidates the iterator/)

def test_element_source_name_then_literal(rows: list[list[int32]],
                                          i: int32) -> None:
    """A name can hold the literal's value, so the pair may alias."""
    for v in rows[i]:
        rows[1].append(v)  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]' may hit the element being iterated.*'append' may invalidate the iterator/)

def test_element_source_literal_then_name(rows: list[list[int32]],
                                          i: int32) -> None:
    """Same the other way round."""
    for v in rows[0]:
        rows[i].append(v)  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]' may hit the element being iterated.*'append' may invalidate the iterator/)

def test_element_source_negative_literal(rows: list[list[int32]]) -> None:
    """`rows[-1]` is `rows[0]` in a one-element list, so it is not distinct."""
    for v in rows[0]:
        rows[-1].append(v)  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]' may hit the element being iterated.*'append' may invalidate the iterator/)

def test_element_source_distinct_literal_setitem(rows: list[list[int32]]) -> None:
    """Element assignment takes the same index rule as a mutating method."""
    for v in rows[0]:
        rows[1] = [v]  # tpyc: ok

def test_element_source_dict_distinct_keys(t: dict[str, list[int32]]) -> None:
    """Str keys that differ are distinct elements too."""
    for v in t["a"]:
        t["b"].append(v)  # tpyc: ok

def test_element_source_dict_same_key(t: dict[str, list[int32]]) -> None:
    for v in t["a"]:
        t["a"].append(v)  # tpyc: warning(/Mutation of 't\[\.\.\.\]' while iterating over it.*'append' invalidates the iterator/)

def test_element_source_setitem(rows: list[list[int32]], i: int32) -> None:
    """Replacing the element destroys the very list being iterated."""
    for v in rows[i]:
        rows[i] = [v]  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]'.*element assignment/)

def test_element_source_outer_mutation(rows: list[list[int32]], i: int32) -> None:
    """The container the element lives in reallocates it away."""
    for v in rows[i]:
        rows.append([v])  # tpyc: warning(/Mutation of 'rows'.*'append'/)

def test_container_source_element_mutation_ok(rows: list[list[int32]]) -> None:
    """Iterating the CONTAINER: mutating an element leaves the iteration valid.

    Replacing an element (`rows[0] = [...]`) is deliberately not a section
    here: it is warning-free for the same reason, but it writes through a
    live element borrow (BUGS.md#setitem-write-under-live-element-borrow),
    so pinning it would pin that defect's current behaviour.
    """
    for row in rows:
        rows[0].append(1)  # tpyc: ok
        row.append(3)      # tpyc: ok

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

def test_slice_source_warns() -> None:
    """A slice iterates a SPAN into the list, so a growing append can still
    reallocate the storage the span names."""
    items: list[int32] = [1, 2, 3]
    total: int32 = 0
    for x in items[0:2]:
        total += x
        items.append(x)  # tpyc: warning(/Mutation of 'items'.*'append'/)

def test_merged_element_loan_unknown_index() -> None:
    """The fourth wording: a NON-iteration loan that is index-uncertain.

    Rebinding the local the iteration borrows through merges the loans, and
    the merged loan keeps `on_element` while losing the index -- so the
    mutation is worded as possible, on the borrowed-element branch rather
    than the iterating one."""
    cube: list[list[list[int32]]] = [[[1, 2]], [[3, 4]], [[5, 6]]]
    rows: list[list[int32]] = []
    rows = cube[0]
    for v in rows[0]:
        rows = cube[1]
        cube[2].append([v])  # tpyc: warning(/Mutation of 'cube\[\.\.\.\]' may hit a borrowed element.*'append' may invalidate references/)
    print("merged loan:", len(cube), len(cube[2]))

def main() -> None:
    test_else_clause_ok()
    test_merged_element_loan_unknown_index()

main()
