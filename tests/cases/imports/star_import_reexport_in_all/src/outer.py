# outer re-exports a name imported from inner. A `__all__` entry that
# names a re-exported import must not be flagged as phantom, even
# though it is not locally defined in this module.
from inner import shared_helper

__all__ = ["shared_helper"]
