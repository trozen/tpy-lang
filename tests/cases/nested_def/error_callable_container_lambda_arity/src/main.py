# Static callable arity checking: docs/LANGUAGE_FEATURES.md, Lambda / Closures.
from typing import Callable
from tpy import int32


def main():
    callbacks: list[Callable[[int32], int32]] = []
    # Own on append's parameter must not hide the concrete signature.
    callbacks.append(lambda x, y: x + y)  # tpyc: error(/Lambda has 2 parameter.*expects 1/)


main()
