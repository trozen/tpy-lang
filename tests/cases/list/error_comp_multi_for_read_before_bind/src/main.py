# A filter reading a name that a LATER `for` clause binds reads that clause's
# variable before it is bound (CPython raises UnboundLocalError).


def main() -> None:
    rows = [[1, 2], [3]]
    x = 1
    print([x for row in rows if x > 0 for x in row])  # tpyc: error(/.x. is read before the .for. clause that binds it/)


main()
