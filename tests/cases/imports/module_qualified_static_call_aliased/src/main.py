# Regression: `import m as alias` followed by `alias.Cls.method()` and
# `alias.Cls.CONST` works. The alias binds the module under a new local name;
# the module-qualified class member dispatch must follow `import_source` to
# the real module name when resolving the record.
import factory as fac


def main() -> None:
    c = fac.Counter.make(11)  # tpyc: ok
    print(c.value)
    print(fac.Counter.LIMIT)  # tpyc: ok


main()
