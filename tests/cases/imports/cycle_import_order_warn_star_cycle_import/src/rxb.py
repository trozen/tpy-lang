# A module of the cycle binds E late: moving that import up fails as well.
from rxa import *  # tpyc: warning(/'rxa' is still being imported -- .* before it binds 'E' \(rxa\.py:4\); CPython's star import does not bind it here, so a use of it raises NameError\. Move the shared definitions into a module outside the cycle\.$/)


class B:
    pass


def use(x: E) -> int:
    return 1
