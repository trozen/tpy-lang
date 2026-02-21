# Unannotated list literal with mixed types should error.
# Union types are not auto-inferred from heterogeneous literals.
from tpy import Int32

class Rect:
    w: Int32

class Circle:
    r: Int32

def main():
    l = [Rect(), Circle()]  # tpyc: error(/mixed types/)

main()
