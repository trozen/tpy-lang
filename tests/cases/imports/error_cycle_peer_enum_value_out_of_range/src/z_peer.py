# Cycle peer sorting after a_user; an int8-backed enum with a value int8 cannot hold.
import enum
import a_user
from tpy import int8


class E(int8, enum.Enum):
    A = 1
    B = 300  # out of range for int8: reported here  # tpyc: error(/value 300 is out of range for int8/)
