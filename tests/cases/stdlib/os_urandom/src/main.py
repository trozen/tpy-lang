# os.urandom returns n random bytes. The bytes themselves are non-deterministic
# (can't byte-compare to CPython), so assert properties: exact length, empty for
# 0, and two draws differ. Byte-compared against CPython.
import os


def main():
    print("len", len(os.urandom(16)))           # 16
    print("len0", len(os.urandom(0)))           # 0
    print("len300", len(os.urandom(300)))       # 300 (crosses the 256 chunk)
    print("differ", os.urandom(16) != os.urandom(16))   # True


main()
