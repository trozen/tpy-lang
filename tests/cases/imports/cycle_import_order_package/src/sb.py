# `spk.a` has started but is not bound on `spk` yet: CPython's from-import
# takes it from the modules being imported.
from spk import a  # tpyc: ok


def grow(e: a.E) -> None:  # tpyc: ok
    e.v += 1
