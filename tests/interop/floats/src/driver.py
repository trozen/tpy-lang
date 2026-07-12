# Shared across the ext-exec and cpy-parity runs. Float has no TPy-specific
# bounds, so every value here -- including the error cases -- behaves
# identically in the compiled extension and the TPy source under CPython.
import floats


def err(f):
    try:
        f()
        return "NO-RAISE"
    except TypeError:
        return "TypeError"
    except OverflowError:
        return "OverflowError"


print(floats.scale(3.0))
print(floats.scale(3))           # int coerces via __float__
print(floats.scale(True))        # bool coerces
print(floats.scale(-2.0))
print(floats.scale(0.0))
print(floats.addf(0.1, 0.2))     # printed by CPython on both paths
print(floats.addf(-1.5, 1.5))
print(err(lambda: floats.scale("x")))      # non-number -> TypeError (both)
print(err(lambda: floats.scale(10**400)))  # int too large for double (both)
