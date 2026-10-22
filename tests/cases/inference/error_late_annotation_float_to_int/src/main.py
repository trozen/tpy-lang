# Refuses valid Python by the rule "Numeric widening across reassignments",
# Annotated (docs/LANGUAGE_FEATURES.md): an annotation goes on a variable's
# first binding, at module level as in a function, so one on a name the
# module already bound is refused.
x = 0.0
x: int = 100  # tpyc: error(/'x' is already bound at line 5; an annotation goes on the first binding/)
