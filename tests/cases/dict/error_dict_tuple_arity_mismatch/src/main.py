# Different-arity tuple keys in a dict literal still produce a
# clean "mixed key types" diagnostic (the literal unification helper
# returns None for arity mismatch).
def main() -> None:
    d = {(1, 2): "a", (3, 4, 5): "b"}  # tpyc: error(/mixed key types/)
    print(d)


main()
