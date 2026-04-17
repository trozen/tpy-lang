# Error: passing a borrowed Box to an Own[Box[...]] parameter at non-last-use
# would need a copy, but Box is non-copyable (__del__ deletes copy ops).
from tplib import Box
from tpy import Own, Int32

def take_box(b: Own[Box[Int32]]) -> None:
    print(b.get())

def fwd(b: Box[Int32]) -> None:
    take_box(b)  # tpyc: error(/non-copyable/)
