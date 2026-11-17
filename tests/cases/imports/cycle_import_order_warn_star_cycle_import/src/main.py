# Star imports from a cycle peer still being imported that binds the name by
# an import: moving that import above the cycle import is the remedy only when
# it loads no module of the cycle, and is not the cycle import itself.
import vca
import vcb
import rxa
import rxb
import rxc
import sla
import slb


def main() -> None:
    vcb.main()
    print("reexport:", rxb.use(rxc.E()))
    slb.main()


main()
