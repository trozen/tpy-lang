
from tpy import Bool


def invert(x: Bool | None) -> Bool:
    return not x  # tpyc: error(/Invalid operand type for 'not'/)
