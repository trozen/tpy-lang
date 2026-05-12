# Star import of a typed module-level global from a user module. Used
# to fail with "PUBLIC_VAL is not a variable" because the star branch
# of sema's bind_imports rebound the namespace slot to IMPORTED_NAME
# after _register_user_module_import had already set it to VARIABLE
# (BUGS.md, pre-existing). Explicit `from lib import PUBLIC_VAL` was
# unaffected because that branch installs in the opposite order.
from lib import *

print(PUBLIC_VAL)
