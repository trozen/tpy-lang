# A COMPREHENSION at a list's Own[element] insert slot: the row beside it
# admits container-returning call rvalues, whose bare render binds the T&&
# slot, and a comprehension's statement-expression has no such row -- so it
# must keep rejecting rather than borrow the call face's render.


def main() -> None:
    table: list[list[float]] = []
    table.append([2.0 * float(i) for i in range(3)])  # tpyc: error(/method.arg_shape/)
    print(len(table))


main()
