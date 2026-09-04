# The adjacent shape to the unannotated nested-list append: the inner list is a
# COMPREHENSION rvalue, not a name, so the Own-slot arg row has no resolved
# source type to compare against the still-unresolved receiver slot.
def main() -> None:
    table = []
    table.append([2.0 * float(i) for i in range(3)])  # tpyc: error(/method.arg_shape/)
    print(len(table))


main()
