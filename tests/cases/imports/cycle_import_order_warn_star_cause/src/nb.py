# `nb` holds both of other's names above the star import: under CPython each
# use reads other's binding.
from other import E, F
from na import *  # tpyc: warning(/'na' is still being imported -- .* before it defines 'E', 'F'; CPython's star import binds none of them here, so a use of them reads the binding they had before that import\. Define them above that import in na\.py, or import 'nb' before 'na' in main\.py\./)


def main() -> None:
    print("held:", E().v, F().w)
