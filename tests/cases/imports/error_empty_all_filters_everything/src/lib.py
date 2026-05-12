# Empty `__all__` means "this module exports nothing via star import".
# The function below is reachable only via an explicit
# `from lib import hidden_via_empty_all`; `from lib import *` brings
# no names into scope.
__all__ = []


def hidden_via_empty_all() -> None:
    print("hi")
