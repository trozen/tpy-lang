# A non-array (conditional) list comprehension whose element is a list literal,
# plus a walrus form. The comp result binds `list[list[int]]`; the embedded
# pending element type must be finalized in the binding or codegen crashes.
def main() -> None:
    rows = [[i, i + 1] for i in range(4) if i > 0]  # tpyc: ok
    print(rows)
    # Mutate through the nested subscript to prove a real mutable container
    # crossed the binding (a silent copy would not be observed).
    rows[0][0] = 99
    print(rows)

    # Walrus binding the same composite (a separate binding sink).
    print((cols := [[j * 2, j * 3] for j in range(3) if j > 0]))  # tpyc: ok
    cols[1][0] = 77
    print(cols)


def loop_mutate() -> None:
    # Plain (non-resumable) for-loop over the nested-list comp: the loop var's
    # element type is the pending inner list, finalized via stmt.elem_type.
    # Mutate through the loop var and observe the change on the source row
    # (reference semantics, not a per-iteration copy).
    rows = [[i, i + 1] for i in range(4) if i > 0]
    for r in rows:
        r[0] = r[0] + 100
    print(rows)


main()
loop_mutate()
