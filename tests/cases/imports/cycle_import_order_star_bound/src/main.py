# Star imports in a cycle that CPython runs as TPy does: the reader defines
# the name itself, already has it from the module being star-imported, or a
# package's `__all__` loads its submodules in an order that works -- no warning.
from apk import *
import ola
import olb
import abp.api
import apk.b


def main() -> None:
    olb.main()
    print("already bound:", abp.api.use())
    print("all order:", apk.b.value())


main()
