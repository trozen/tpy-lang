from sra import *  # tpyc: warning(/'sra' is still being imported -- .* before it binds 'date' \(sra\.py:3\); CPython's star import does not bind it here.*Move the import at sra\.py:3 above that import/)


def f(d: date) -> int:
    return d.year
