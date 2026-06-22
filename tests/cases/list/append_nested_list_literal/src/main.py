# Appending a list literal to a list whose element type is itself a (pending)
# list literal: the two pending list types must compare compatible at sema.
def take(g: list[list[int]]) -> None:  # tpyc: ok
    print(g)


def main() -> None:
    rows = [[i, i + 1] for i in range(3)]
    rows.append([9, 9])  # tpyc: ok
    print(rows)
    # Mutate a row in place to prove the stored sublists are real containers.
    rows[0][0] = 99
    print(rows)
    take([[1, 2], [3, 4]])  # param-passing sibling

    # Both rows pending with int-LITERAL elements (not Int32): the element
    # predicate must accept two distinct literals, like peer-unification does.
    lits = [[1, 2]]
    lits.append([3, 4])  # tpyc: ok
    print(lits)

main()
