# Unannotated list literal with mixed types should error.
# Union types are not auto-inferred from heterogeneous literals.
from tpy import int32

class Rect:
    w: int32

class Circle:
    r: int32

def main():
    l = [Rect(), Circle()]  # tpyc: error(/mixed types/)

main()
