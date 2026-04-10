# Error: recursion through fixed-size tuple (no heap indirection)
type Bad = str | tuple[Bad, int]  # tpyc: error(/direct recursion/)
