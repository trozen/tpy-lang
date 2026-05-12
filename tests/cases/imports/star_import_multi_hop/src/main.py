# Star-import chain: c defines LIMIT, b does `from c import *`,
# main does `from b import *`. Exercises compile-time star expansion
# at two hops -- b's expansion must run before main's so b's
# `module_attributes` carries LIMIT by the time main expands.
from b import *

print(LIMIT)
