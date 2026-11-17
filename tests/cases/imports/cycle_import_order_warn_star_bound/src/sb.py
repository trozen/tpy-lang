# `sb` star-imported other's E above its star import of the suspended `sa`:
# CPython keeps other's E, while TPy's second star import binds sa's.
from other import *
from sa import *  # tpyc: warning(/'sa' is still being imported -- .* before it defines 'E'; CPython's star import does not bind it here, so a use of it reads the binding it had before that import\./)


def main() -> None:
    print("earlier star:", E().v)
