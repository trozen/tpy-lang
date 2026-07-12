# Shared across the ext-exec and cpy-parity runs: a data-carrying user exception
# crosses as its own type (catchable as its ValueError base) and every data
# field crosses as an instance attribute -- all agree with CPython. str(e)/args
# diverge (message-only reconstruction) and are asserted ext-only in
# ext_checks.py.
import userexcd

try:
    userexcd.parse(-1)
    raise AssertionError("expected ParseError")
except userexcd.ParseError as e:
    print(type(e).__name__)
    print(isinstance(e, ValueError))
    print(e.line)
    print(e.detail)
    print(e.payload)
    print(e.severity == userexcd.Severity.FATAL)
    print(e.severity is userexcd.Severity.FATAL)  # singleton identity preserved

print(userexcd.parse(5))
