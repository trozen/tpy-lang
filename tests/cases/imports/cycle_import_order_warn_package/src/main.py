# Import-time reads within a package whose parts are still being imported:
# a submodule attribute of its package, absolute and relative from-imports,
# a from-list that loads a submodule before it binds.
import pkg.a
import rp.a
import la


def main() -> None:
    pkg.a.main()
    rp.a.main()
    la.main()


main()
