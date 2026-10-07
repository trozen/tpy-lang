# A capture an inner `for` clause of a generator expression iterates is held
# across pulls, so the loop the genexpr feeds may not rebind it.


def main() -> None:
    a_list = [1, 2]
    b_list = [10, 20]
    for v in (a + b for a in a_list for b in b_list):  # tpyc: error(/.b_list. is iterated by this generator expression, but the loop it feeds rebinds it between pulls; bind it to a local first/)
        print(v)
        b_list = [v]


main()
