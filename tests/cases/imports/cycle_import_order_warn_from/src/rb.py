from ra import B  # tpyc: warning(/'B' is imported from 'ra' while 'ra' is still being imported.*ImportError/)
from tpy import Own


def make() -> Own[B]:
    return B(8)
