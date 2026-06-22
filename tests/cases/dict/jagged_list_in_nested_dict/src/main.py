# A jagged pending list nested inside a concrete dict value, where the inner
# dicts are sibling values of an outer dict: convergence must reach the pending
# list through the concrete dict[K, V] wrapper (a non-empty dict literal is a
# concrete dict whose value is still pending). Mutating it must be observed.
def main() -> None:
    d = {1: {10: [1, 2]}, 2: {20: [3, 4, 5]}}  # tpyc: ok
    print(d)
    d[2][20].append(9)  # inner list is a real vector reached through the dicts
    print(d)

main()
