# Cycle peer sorting after a_user; its enum repeats a member name.
import enum
import a_user


class E(enum.Enum):
    A = 1
    B = 2
    A = 3  # the duplicate is reported here, not where a_user reads the enum  # tpyc: error(/Duplicate enum member name: 'A'/)
