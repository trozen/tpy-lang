from tpy import int32
from nonexistent import foo  # tpyc: error(/Module 'nonexistent' not found/)  # tpyc: error(/Module 'nonexistent' not found/)

def main() -> int32:
    return foo()
