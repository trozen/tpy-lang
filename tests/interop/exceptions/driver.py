# Shared across the ext-exec and cpy-parity runs. A body-raised TPy exception
# crosses the boundary as the matching CPython type with its message intact, so
# `except <SpecificType>` works and `str(e)` agrees with the TPy source running
# under CPython -- covering both user-raised exceptions (literal message) and a
# runtime-raised ZeroDivisionError from `//` (whose message matches CPython's).
import excs


def show(f, *args):
    try:
        r = f(*args)
        print("ok", r)
    except Exception as e:
        # KeyError str() adds quotes on both paths -- pinned here on purpose.
        print(type(e).__name__, "|", e)


show(excs.check_positive, -1)   # ValueError | must be non-negative
show(excs.check_positive, 5)    # ok 5
show(excs.lookup, 2)            # KeyError | 'no such key'
show(excs.lookup, 1)            # ok 100
show(excs.open_missing)         # FileNotFoundError | missing file
show(excs.fail_generic)         # Exception | generic failure
show(excs.divide, 10, 3)        # ok 3
show(excs.divide, 10, 0)        # ZeroDivisionError | integer division or modulo by zero
