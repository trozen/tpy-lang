# Error: direct recursion without indirecting container
type Bad = str | Bad  # tpyc: error(/direct recursion/)
