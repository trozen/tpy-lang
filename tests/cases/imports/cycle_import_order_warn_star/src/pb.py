# CPython's star import binds none of E, F, main here: `raise E()` and the
# annotation `x: F` raise NameError there. Reordering main.py fixes it.
from pa import *  # tpyc: warning(/CPython cannot import this as TPy does: 'pa' is still being imported -- .* before it defines 'E', 'F', 'main'; CPython's star import binds none of them here, so a use of them raises NameError\. Define them above that import in pa.py, or import 'pb' before 'pa' in main.py\./)


def use() -> int:
    try:
        raise E()
    except E:
        return 1


def use2(x: F) -> int:
    return x.v
