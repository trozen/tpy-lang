# A diagnostic inside an `except (A, B):` body is reported ONCE, not once per
# expanded arm. The parser clones the body per element and sema analyzes each
# clone, so without deduplication the same warning would print twice against the
# one source line the user wrote. The warning below is independent of the bound
# type, so both clones emit it identically -- the shape dedup must collapse.
class Plain:
    pass


class PSub(Plain):
    pass


class AErr(Exception):
    pass


class BErr(Exception):
    pass


def boom(which: int) -> None:
    if which == 0:
        raise AErr()
    raise BErr()


def caught(which: int, p: Plain) -> None:
    try:
        boom(which)
    except (AErr, BErr):
        # Folds to False in both clones -> one warning, not two.
        if isinstance(p, PSub):  # tpyc: warning(/folds to False/)
            print("unreachable")
        print("caught", which)


def main() -> None:
    p = Plain()
    for i in range(2):
        caught(i, p)


main()
