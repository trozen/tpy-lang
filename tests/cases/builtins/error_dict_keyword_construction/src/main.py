# dict(x=pairs) builds {"x": pairs} in CPython; TPy does not build a dict
# from keywords, and says so rather than reading it as dict(pairs)
# (BUGS.md#builtin-keyword-construction-rejects).


def main() -> None:
    pairs = [("a", 1)]
    d = dict(x=pairs)  # tpyc: error(/keyword arguments to dict\(\) are not supported; write a dict literal/)
    print(d)


main()
