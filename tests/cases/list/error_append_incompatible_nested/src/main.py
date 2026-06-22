# Inverse: a str-element sublist appended to a list of int sublists is still
# rejected -- the pending-compat rule accepts unifiable peers, not cross-category.
def main() -> None:
    rows = [[1, 2], [3, 4]]
    rows.append(["a", "b"])  # tpyc: error(/Type mismatch in argument 'value'/)
    print(rows)

main()
