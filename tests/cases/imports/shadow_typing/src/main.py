# Test warning when user class shadows an imported typing name
from typing import Sized
from tpy import Int32

class Sized:  # tpyc: warning(/shadows import from 'typing'/)
    val: Int32
    def __init__(self, v: Int32):
        self.val = v

def main():
    s = Sized(Int32(42))
    print(s.val)

main()
