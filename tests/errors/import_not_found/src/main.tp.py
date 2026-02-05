from tpy import Int32
from nonexistent import foo  # tpyc: error(/Module 'nonexistent' not found/)  # tpyc: error(/Module 'nonexistent' not found/)

def main() -> Int32:
    return foo()
