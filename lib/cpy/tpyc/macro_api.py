"""
CPython stub for TurboPython Macro API.

Macro modules are not supported under CPython. They run at compile time only
via the real tpyc.macro_api in the compiler. This stub exists so that importing
tpyc.macro_api doesn't fail at module level, but any actual use will raise.
"""

raise ImportError(
    "tpyc.macro_api is not available under CPython. "
    "Macro modules run at compile time only."
)
