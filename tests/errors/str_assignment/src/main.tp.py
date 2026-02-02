from tpy import Int32

def modify_str(s: str) -> None:
    s[0] = "x"  # tpyc: error(/Cannot assign to elements of str/)

def main() -> Int32:
    return 0
