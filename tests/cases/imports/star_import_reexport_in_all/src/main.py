# Star-import through a re-exporter: outer's __all__ lists
# shared_helper which outer doesn't define directly but imports from
# inner. Two regression guards in one case:
#   1. outer's __all__ = ["shared_helper"] must compile cleanly
#      without phantom-name warning.
#   2. `from outer import *` must bring shared_helper into scope.
from outer import *

shared_helper()
