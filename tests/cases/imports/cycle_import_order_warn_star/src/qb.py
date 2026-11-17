from qa import *  # tpyc: warning(/'qa' is still being imported .* before it defines 'Q', 'main'; CPython's star import binds none of them here/)
from tpy import Own


def make() -> Own[Q]:
    return Q(3)
