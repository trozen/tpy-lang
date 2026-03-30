# Error: raise <expr> with primitive type
def test() -> None:
    x = 42
    raise x  # tpyc: error(/cannot raise.*Int32/)
