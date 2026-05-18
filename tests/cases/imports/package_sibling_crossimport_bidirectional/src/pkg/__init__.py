# Both siblings re-exported from a package whose submodules
# bidirectionally cross-import. Exercises the descendant-submodule
# suppression on every re-export kind under cycle-peer conditions.
from pkg.a import A, with_b
from pkg.b import B
