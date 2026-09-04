# The list-REPEAT rvalue at a container element slot: routed as a
# comprehension element, but the method-ARG slot has no row for it, so the
# append keeps rejecting.
def main() -> None:
    n = 3
    rows: list[list[float]] = []
    for _ in range(2):
        rows.append([0.0] * n)  # tpyc: error(/method.arg_shape/)
    print(len(rows), len(rows[0]))


main()
