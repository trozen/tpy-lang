# A leftover `# tpy:` directive comment is an error pointing at `__tpy__`,
# never a comment that silently stops declaring anything.
# Valid Python, rejected as a documented restriction (docs/LANGUAGE_FEATURES.md,
# Module Settings: `__tpy__`).
# tpyc: error(/'# tpy:' directives were replaced by __tpy__/)
# tpy: cpp_namespace("app")


def main() -> None:
    print("unreachable")


main()
