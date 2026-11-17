# The causes and results a star-import warning names when a cycle peer is
# still being imported: some names held from above and some not, all held,
# a name bound by a late import, and `__all__` names defined late or only
# declared. TPy warns at each star import and runs the program.
import ma
import mb
import na
import nb
import ra
import rb
import ja
import jb


def main() -> None:
    mb.main()
    nb.main()
    rb.main()
    jb.main()


main()
