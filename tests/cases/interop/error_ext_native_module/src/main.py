# An ext module and a native module are both values of the one "mod" key, so
# declaring both is a duplicate key: the old ext+native contradiction (a
# PyInit_-less, unimportable .so) can no longer be written.
# Valid Python, rejected as a documented restriction (docs/LANGUAGE_FEATURES.md,
# Module Settings: `__tpy__`).
__tpy__ = {"mod": "ext",
           "mod": "native"}  # tpyc: error(/duplicate key 'mod'/)
