# Variable-length nested list literals: jagged sublists are compatible (the
# inners demote to list[T]), unlike the equal-size case that stays Array.
def main() -> None:
    xs = [[1, 2], [3, 4, 5]]  # tpyc: ok
    print(xs)
    xs[0].append(9)  # inner is a real vector -> grows; change is observed
    print(xs)

    rows = [[1, 2]]
    rows.append([3, 4, 5])  # tpyc: ok
    print(rows)

    deep = [[[1, 2]], [[3, 4, 5]]]  # tpyc: ok  (recursive demotion, deeper jagged)
    deep[1][0].append(9)  # innermost is a real vector reached through the nesting
    print(deep)

main()
