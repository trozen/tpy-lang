from tpy import Int32, Span

def bad() -> Span[Int32]:
    data = [1, 2, 3]
    return data  # tpyc: error(/Cannot return local or temporary value/)
