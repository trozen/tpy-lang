from tpy import int32

def modify_str(s: str) -> None:
    s[0] = "x"  # tpyc: error(/Type 'str' does not support item assignment/)

def main() -> int32:
    return 0
