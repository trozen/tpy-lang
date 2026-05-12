# Pins the BUGS.md gap: `__all__ = []` (the Python idiom for "export
# nothing") fails the generic list type-inference path instead of
# being recognized as a valid empty literal. Workaround documented
# in LANGUAGE_FEATURES.md is `__all__: list[str] = []`.
#
# When this is fixed, the diag snapshot will change -- bump the
# snapshot together with the fix and treat the bump as the signal
# that the BUGS.md entry can be retired.
__all__ = []  # tpyc: error(/Cannot infer element type for list/)


def foo() -> None:
    pass
