from . import X  # tpyc: warning(/'X' is imported from 'rp' while 'rp' is still being imported.*ImportError/)


def make() -> int:
    return X(3).v
