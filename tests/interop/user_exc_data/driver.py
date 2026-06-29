# Shared across the ext-exec and cpy-parity runs: only the exception TYPE, which
# crosses faithfully and agrees with CPython. For a data-carrying exception even
# str(e) diverges (CPython keeps the full constructor args tuple; the boundary
# reconstructs from the message field alone), so the message/args and the dropped
# data field are ext-only and live in ext_checks.py.
import userexcd

try:
    userexcd.parse(-1)
except ValueError as e:   # crosses as ParseError, catchable as its ValueError base
    print(type(e).__name__)

print(userexcd.parse(5))
