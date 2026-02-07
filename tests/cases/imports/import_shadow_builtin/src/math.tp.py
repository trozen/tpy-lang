# User module that shadows builtin 'math'
from tpy import Int32

def custom_add(x: Int32, y: Int32) -> Int32:
    return x + y

MAGIC: Int32 = 42
