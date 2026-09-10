# Test that open() panics for unsupported binary mode strings
from tpy import open_binary

def main() -> None:
    f = open_binary("tpy_test_badmode.bin", "zb")
    f.close()

main()
