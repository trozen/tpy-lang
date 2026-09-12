# Shared across the ext-exec and cpy-parity runs: each exposed Final constant
# reads back as the same value and Python type as the plain source binding.
# TAG (a char Final) is deliberately not touched here -- it is not exposed by
# the .so (see ext_checks.py), so referencing it would diverge from the source.
import constants

print(constants.MAX_SIZE, constants.MIN_SIZE, constants.BIG)
print(constants.RATIO)
print(constants.ENABLED, constants.DISABLED)
print(repr(constants.GREETING), repr(constants.EMPTY))
print(type(constants.MAX_SIZE).__name__, type(constants.RATIO).__name__,
      type(constants.ENABLED).__name__, type(constants.GREETING).__name__)
