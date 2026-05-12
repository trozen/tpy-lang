# Star import of an enum from a user module. Same root cause as the
# typed-global case (BUGS.md): _register_user_module_import calls
# bind_enum, which would have been clobbered by the post-register
# bind_imported_name fall-through. Regression guard for the bind
# order fix in sema/analyzer.py:bind_imports.
from lib import *

c = Color.GREEN
print(c.value)
