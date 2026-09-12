# Test warning when user class shadows an imported typing name
from typing import Sized
from tpy import int32

class Sized:  # tpyc: warning(/shadows import from 'typing'/)
    val: int32
    def __init__(self, v: int32):
        self.val = v

def main():
    s = Sized(int32(42))
    print(s.val)

main()
