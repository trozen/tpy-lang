# Import cycles CPython imports: what a cycle member reads of a peer that is
# still being imported is bound by then, or is read only later -- no warning.
import t2
import t1
import a


def main() -> None:
    t2.main()
    t1.main()
    a.main()


main()
