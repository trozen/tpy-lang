# A bare collection literal assigned via subscript (`d[k] = [..]`, `m[i] = [..]`)
# must materialize to its slot's storage type so it can deduce the forwarding-ref
# value parameter of the __setitem__ template. The final block uses a proven
# bounds-safe index, which takes the `x[i] = value` lvalue path instead -- a bare
# brace-init binds there directly, so that path is intentionally left unwrapped.
# Mutating the stored list after assignment forces the value-vs-reference
# distinction -- a silent copy would not be observable on read alone.
from tpy import int32


def main() -> None:
    groups: dict[str, list[int32]] = {}
    groups["odds"] = [1, 3, 5, 7]  # tpyc: ok
    groups["odds"].append(9)
    print(len(groups["odds"]), groups["odds"][4])

    # Non-trivial element expressions must survive the type-prefix wrap.
    a = 10
    b = 20
    groups["calc"] = [a, b + 1, a * 2]  # tpyc: ok
    print(groups["calc"][0], groups["calc"][1], groups["calc"][2])

    # Same hazard via an integer index into a list-of-lists.
    matrix: list[list[int32]] = [[0]]
    matrix[0] = [1, 2, 3]  # tpyc: ok
    matrix[0].append(4)
    print(len(matrix[0]), matrix[0][3])

    # Proven bounds-safe index: takes the `x[i] = value` lvalue path, which
    # binds a bare brace-init directly (no type prefix). Guards that the
    # deliberately-unwrapped path stays correct.
    rows: list[list[int32]] = [[0], [0], [0]]
    for i in range(len(rows)):
        rows[i] = [i, i + 1]  # tpyc: ok
    print(len(rows), rows[2][1])


main()
