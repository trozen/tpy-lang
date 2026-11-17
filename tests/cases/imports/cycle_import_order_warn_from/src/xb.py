# `rt` is imported only for the C++ build: BUGS.md#cycle-reexported-global-missing-include.
import rt  # noqa: F401
from xa import B  # tpyc: ok
from xa import Late  # tpyc: warning(/'Late' is imported from 'xa' while 'xa' is still being imported.*before it binds 'Late' \(xa.py:5\).*\. Move the import at xa\.py:5 above that import/)
from xa import y  # tpyc: warning(/'y' is imported from 'xa'.*'y' is only declared \(xa.py:6\), which binds nothing.*Give 'y' a value in a module outside the cycle and import it from there\./)
# `xa` has `z` from a finished module that only declares it.
from xa import z  # tpyc: warning(/'z' is imported from 'xa'.*'z' is only declared \(rt.py:7\), which binds nothing.*Give 'z' a value in rt.py\./)


def read() -> int:
    return B(1).k + Late(10).k + y + z
