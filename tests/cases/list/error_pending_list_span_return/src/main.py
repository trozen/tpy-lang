from tpy import int32, Span

def bad() -> Span[int32]:
    data = [1, 2, 3]
    return data  # tpyc: error(/Cannot return local or temporary value/)
