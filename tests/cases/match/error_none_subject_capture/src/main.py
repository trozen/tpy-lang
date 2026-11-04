# A capture pattern on a match subject that is always None is refused with a
# located error (BUGS.md#match-none-subject-capture-rejects).


def show(x: None) -> None:
    match x:  # tpyc: error(/capture pattern on a value that is always None/)
        case v:
            print(v)


show(None)
