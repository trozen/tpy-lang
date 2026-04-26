# Calling a generic function over Iterable[T] with an empty list and no
# @type_param_default(T=...) -- the only arg-side evidence is the empty
# literal's UnknownElementType, so inference fails. Surfaces the generic
# "Cannot infer type arguments" diagnostic, not the indirect pending-list
# resolver error, because the inference step now treats UnknownElementType
# as failure when no default applies.
from typing import Iterable


def f[T](xs: Iterable[T]) -> str:
    return "ok"


def main() -> None:
    print(f([]))  # tpyc: error(/Cannot infer type arguments for 'f'/)


main()
