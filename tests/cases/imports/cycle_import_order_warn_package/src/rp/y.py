from la import X  # tpyc: warning(/'X' is imported from 'la' while 'la' is still being imported -- main.py imports 'la', and 'la' imports 'rp.y' \(la.py:3\) before it binds 'X' \(la.py:3\).*ImportError.*\. Move the shared definitions into a module outside the cycle, or import 'rp.y' before 'la' in main\.py\./)


def f() -> int:
    return X(6).v
