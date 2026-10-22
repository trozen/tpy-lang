# Refuses valid Python by the rule "Numeric widening across reassignments",
# Annotated (docs/LANGUAGE_FEATURES.md): an annotation goes on a variable's
# first binding, at module level as in a function, so one on a name the
# module already bound is refused.
from tpy import int32

x: int32 = int32(1)
x: int = 2  # tpyc: error(/'x' is already bound at line 7; an annotation goes on the first binding/)
