# `width` is an unannotated MODULE-level binding and exports; `scratch` is a
# function local of the same flavour and does not.
from tpy import int32

width = 800


def compute() -> int32:
    scratch = width * 2
    return scratch
