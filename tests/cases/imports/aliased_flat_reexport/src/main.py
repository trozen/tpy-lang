# Aliased re-export through a non-facade intermediate. `b` does
# `from c import Original as MyCls`, then main pulls `MyCls`.
from b import MyCls

x = MyCls(7)
print(x.val)
