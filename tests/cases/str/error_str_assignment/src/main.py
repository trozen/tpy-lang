from tpy import int32

def modify_str(s: str) -> None:
    s[0] = "x"  # tpyc: error(/Cannot assign to elements of str/)

def main() -> int32:
    return 0
