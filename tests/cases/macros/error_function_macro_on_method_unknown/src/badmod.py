# tpy: macro_module
"""A macro module whose exported name is NOT a registered @function_macro --
decorating a method with it must fail at sema, not be silently dropped."""


def not_a_macro(fn):
    return fn
