from .a import Item  # tpyc: warning(/'Item' is imported from 'rp.a' while 'rp.a' is still being imported.*ImportError/)


def f() -> int:
    return Item(2).n
