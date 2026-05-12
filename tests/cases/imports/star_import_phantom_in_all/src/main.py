# `__all__` listing a name the source module does not actually define
# is a typo trap: CPython raises `AttributeError: module 'lib' has no
# attribute 'phantom_name'` at star-import time. TPy emits a warning
# on the defining module (lib.py) -- catches the typo even if nobody
# star-imports lib. Compilation continues, real_fn works, the phantom
# name is simply absent from scope.
#
# Skipping CPython because the runtime mechanism differs (CPython
# aborts main at `from lib import *`; TPy warns at lib's compile step
# and proceeds).
from lib import *

real_fn()
