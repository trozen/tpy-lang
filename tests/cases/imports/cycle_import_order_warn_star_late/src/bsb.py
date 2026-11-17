from bsa import *  # tpyc: warning(/'bsa' is still being imported -- .* before it defines 'zip'; CPython's star import does not bind it here/)


# CPython reads the default off the builtin `zip` and raises AttributeError.
def f(x: "zip" = zip.X) -> str:
    return x.name
