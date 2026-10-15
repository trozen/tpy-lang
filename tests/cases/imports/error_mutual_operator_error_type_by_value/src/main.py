# An operator's peer `@error_return(E)` returns `E` by value (docs/LANGUAGE_FEATURES.md
# "Circular imports"). `a` imports `b` late: `b` imports `E` from the partially
# initialized `a`, so with the import at the top CPython raises ImportError.
import a

print(a.run())
