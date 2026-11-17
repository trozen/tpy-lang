from wa import E  # tpyc: warning(/'E' is imported from 'wa' while 'wa' is still being imported.*ImportError/)
# The star import reads `__all__`, which `wa` bound, and E, which it did not.
from wa import *  # tpyc: warning(/`from wa import \*` reads 'E', 'main', listed in `__all__` of 'wa'.*AttributeError/)


def catch() -> int:
    try:
        raise E()
    except E:
        return 1
