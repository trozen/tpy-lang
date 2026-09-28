# Unpacking inside a list display is not supported
# (BUGS.md#star-unpack-and-multidim-slice-rejected); the rejection names the
# construct and quotes the user's text instead of a Python AST class.
def main() -> None:
    xs = [1, 2]
    ys = [*xs, 3]  # tpyc: error(/unpacking with '\*' \('\*xs'\) is not supported here/)
    print(ys)


main()
