# A store into a nested list whose value no __setitem__ variant takes is the
# assignment mismatch against the element type, not "No matching overload".


def main() -> None:
    g = [[1]]
    g[0] = ["a"]  # tpyc: error(/Type mismatch in assignment: expected list\[int32\], got list\[str\]/)
    print(g)


main()
