# The entry module cannot be part of an import cycle: a_user imports it
# back, and the error stands at a_user's `import main`.
import enum
import a_user


class E(enum.Enum):
    A = 1
    B = 2


class D:
    e: E = E.A
