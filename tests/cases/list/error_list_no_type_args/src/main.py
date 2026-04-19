# Bare `list` annotation (no type args) must be rejected with an arity
# diagnostic. Pins the no-import variant: the parser auto-resolves `list`
# via builtins/__all__ (like CPython's implicit builtins import) and attaches
# `_module_qname="builtins.list"`, so sema's validate_type finds the record
# and errors on missing type args. Paired with error_list_no_type_args_imported
# (explicit `from builtins import list`) -- both must produce the same error.

def plain_bare(x: list) -> None:  # tpyc: error(/requires.*type argument|Generic record 'list' requires/)
    pass
