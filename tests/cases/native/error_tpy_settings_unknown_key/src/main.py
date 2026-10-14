# A misspelled `__tpy__` setting is an error naming the known keys, never a
# silently ignored entry.
# Valid Python, rejected as a documented restriction (docs/LANGUAGE_FEATURES.md,
# Module Settings: `__tpy__`).
__tpy__ = {"cpp_namspace": "app"}  # tpyc: error(/unknown setting 'cpp_namspace' \(known: mod, cpp_namespace, includes, links\)/)


def main() -> None:
    print("unreachable")


main()
