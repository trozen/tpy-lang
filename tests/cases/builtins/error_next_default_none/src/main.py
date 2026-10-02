# next(it, None) is refused: the default has the element's type, and a
# "T or None" result is not supported yet (docs/LANGUAGE_FEATURES.md, the
# builtins section; TODO.md, "Builtin functions: the remaining gaps", item
# 2). CPython returns None once the iterator is done.
def main() -> None:
    xs = [3, 1, 2]
    it = iter(xs)
    v = next(it, None)  # tpyc: error(/No matching overload for next\(.*None\): type parameter T inferred as int32 and None/)
    print(v)


main()
