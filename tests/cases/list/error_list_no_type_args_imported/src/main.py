# Counterpart to error_list_no_type_args: bare `list` with explicit import
# from builtins takes a different path in validate_type (record_info lookup
# rather than factory lookup), but the end result -- an arity diagnostic --
# must be the same.
from builtins import list

def takes_list(x: list) -> None:  # tpyc: error(/requires.*type argument|Generic record 'list' requires/)
    pass
