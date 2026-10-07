# An inner clause's iterable reading a name a LATER clause binds is refused
# (CPython raises UnboundLocalError there).


def main() -> None:
    rows = [[[1, 2]], [[3]]]
    b = [7]
    print([x for a in rows for x in b for b in a])  # tpyc: error(/.b. is read before the .for. clause that binds it/)


main()
