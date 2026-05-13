# Regression: `import m` followed by `m.Cls.method()` and `m.Cls.CONST` works.
# Previously the static-method dispatcher only handled bare-name TpyName
# receivers; TpyFieldAccess(module_var, ClassName) fell through every branch
# and surfaced as "'<module>' is not a variable".
import factory


def main() -> None:
    c = factory.Counter.make(7)  # tpyc: ok
    print(c.value)
    print(factory.Counter.LIMIT)  # tpyc: ok


main()
