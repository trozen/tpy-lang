# Static callable result checking: docs/LANGUAGE_FEATURES.md, Lambda / Closures.
from typing import Callable
from tpy import int32


def main():
    callbacks: dict[str, Callable[[int32], int32]] = {}
    # The expected integer result must still reject a string body.
    callbacks["run"] = lambda x: "wrong"  # tpyc: error(/Lambda body type.*not compatible.*int32/)


main()
