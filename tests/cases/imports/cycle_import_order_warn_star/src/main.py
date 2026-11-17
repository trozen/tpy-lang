# Star imports from a cycle peer still being imported, which binds names it
# exports only later: TPy's star import binds them all, CPython's does not,
# so TPy warns at the star import and runs the program.
import pa
import qa
import rc


def main() -> None:
    pa.main()
    qa.main()
    rc.main()


main()
