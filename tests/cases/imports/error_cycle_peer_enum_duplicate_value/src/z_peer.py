# Cycle peer sorting after a_user; its enum repeats a value.
import enum
import a_user


class E(enum.Enum):
    A = 1
    B = 2
    C = 1  # the duplicate is reported here, not where a_user reads the enum  # tpyc: error(/Duplicate enum value 1/)
