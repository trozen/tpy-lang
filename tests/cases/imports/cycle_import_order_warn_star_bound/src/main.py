# Star imports from a cycle peer still being imported, where TPy's star import
# binds a name CPython's does not: over a binding the reader already holds, or
# a name only declared. TPy warns at the star import and runs the program.
import ea
import eb
import sa
import sb
import da
import db
import xa
import xb


def main() -> None:
    eb.main()
    sb.main()
    db.main()
    xb.main()


main()
