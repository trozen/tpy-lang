# Shared across the ext-exec and cpy-parity runs: only values that match
# between the compiled extension and the TPy source under CPython. bool args --
# and the truthiness coercions that survive `not` / `and` -- agree on both
# paths; coercions observable only through identity (return-the-arg) diverge and
# live in ext_checks.py.
import bools

print(bools.flip(True))
print(bools.flip(False))
print(bools.flip(0))       # 0 is falsy: `not 0` == `not False` == True (both)
print(bools.flip([]))      # [] is falsy: True (both)
print(bools.both(True, True))
print(bools.both(True, False))
print(bools.both(False, True))
print(bools.identity(True))
print(bools.identity(False))

# void-return: tally returns None; its effect is read back as an int64.
print(repr(bools.tally(True)))
print(repr(bools.tally(False)))
bools.tally(True)
bools.tally(1)             # truthy coerces -> the True branch runs on both paths
print(bools.true_count())  # 3
