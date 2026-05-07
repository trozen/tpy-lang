# `__all__` filters star imports but does NOT restrict explicit
# imports. lib defines Public and Hidden; only Public is in __all__,
# so star import sees just it. Explicit `from lib import Hidden`
# still works.
from lib import *
from lib import Hidden

print(Public().val)
print(Hidden().val)
