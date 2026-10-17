# A recursive union alias, declared in its own module like the stdlib's.
from tpy import int32

type Tree = int32 | list[Tree]
