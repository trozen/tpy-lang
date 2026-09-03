# A container ternary used as a SUBSCRIPT receiver: `(a if c else b)[0]`.
# The select must alias the chosen container, not copy it, so each read is
# repeated after mutating the container the condition picks.


def main() -> None:
    a = [1, 2]
    b = [3, 4]
    a.append(9)
    b.append(9)
    c = True
    print((a if c else b)[0])  # tpyc: ok
    a[0] = 42
    # Reads 42 only if the ternary selected the live `a`.
    print((a if c else b)[0])  # tpyc: ok
    c = False
    b[1] = 77
    print((a if c else b)[1])  # tpyc: ok

    d1 = {"k": 1}
    d2 = {"k": 2}
    flag = False
    # The same receiver row over a dict, and a non-literal index.
    print((d1 if flag else d2)["k"])  # tpyc: ok
    d2["k"] = 8
    print((d1 if flag else d2)["k"])  # tpyc: ok


main()
