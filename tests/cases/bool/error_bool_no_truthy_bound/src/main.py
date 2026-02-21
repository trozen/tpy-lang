# Test that bool() on unbounded generic T produces an error
def check[T](x: T) -> bool:
    return bool(x)  # tpyc: error(/cannot convert/)
