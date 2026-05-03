# `import pkg.sub` followed by attribute access on a variable in the
# submodule (`pkg.sub.X`). Pre-fix: sema rejected with
# "Undefined variable: 'pkg'" even though the import succeeded.
# Function-call form (`pkg.sub.fn()`) already worked.
import pkg.state


def main() -> None:
    print(pkg.state.LIMIT)
    print(pkg.state.banner)


main()
