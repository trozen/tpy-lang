# Using @native_c without importing it gives a helpful error.
from tpy import Int32

@native_c  # tpyc: error(/Unknown decorator 'native_c'/)
def get_value() -> Int32:
    ...

def main() -> None:
    pass
