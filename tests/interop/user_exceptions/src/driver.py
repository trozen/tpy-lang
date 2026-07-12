# Shared across the ext-exec and cpy-parity runs. A body-raised user exception
# crosses as its own Python type with its message, and stays catchable as its
# built-in base (and its user base) -- identical to the TPy source under CPython.
import userexc


def show(f, *a):
    try:
        r = f(*a)
        print("ok", r)
    except Exception as e:
        print(type(e).__name__, "|", e)


show(userexc.lookup, 2)      # NotFound | 'no such key'  (KeyError-derived: quoted)
show(userexc.lookup, 1)      # ok 100
show(userexc.boot, 0)        # ConfigError | bad config
show(userexc.boot, 1)        # ok 1
show(userexc.fail_app)       # AppError | app failure
show(userexc.must_be_even, 3)  # ValueError | must be even  (built-in: registry miss -> cascade)
show(userexc.must_be_even, 4)  # ok 4

# Inheritance is preserved: catchable as the built-in base and the user base.
try:
    userexc.lookup(2)
except KeyError:
    print("caught NotFound as KeyError")

try:
    userexc.boot(0)
except userexc.AppError:
    print("caught ConfigError as AppError")

# FatalError inherits BaseException directly, so `except Exception` must NOT
# catch it (regression guard for the py_exc_by_name BaseException base).
try:
    userexc.abort_now()
except Exception:
    print("WRONG: FatalError caught as Exception")
except BaseException:
    print("caught FatalError as BaseException, not Exception")
