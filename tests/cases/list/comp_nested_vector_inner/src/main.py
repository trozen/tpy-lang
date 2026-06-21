# Nested list comprehensions whose inner lists are vectors (not fixed arrays):
# a variable-size inner comprehension yields jagged sublists, and an annotated
# list[list[int]] lets an inner row grow via append -- the growable counterpart
# to the array-inner cases (comp_nested_list_literal / comp_nested_list_cond).
def jagged() -> None:
    rows = [[k for k in range(i)] for i in range(4)]  # lengths 0,1,2,3
    print(rows)
    # Inner is a real vector: grow one row through the outer subscript and
    # observe it (a fixed array would reject push_back at the C++ level).
    rows[2].append(99)
    print(rows)


def annotated_growable() -> None:
    rows: list[list[int]] = [[i, i + 1] for i in range(3)]
    rows[0].append(99)
    print(rows)


jagged()
annotated_growable()
