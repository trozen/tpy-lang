# A recursive union alias imported by the case under another name.
from tpy import int32

type Tree = int32 | list[Tree]
