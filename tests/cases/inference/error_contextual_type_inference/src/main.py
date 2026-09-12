# Contextual inference still fails when no context is available.
from tpy import int32, Own

def make_empty[T]() -> Own[list[T]]:
    return []

x = make_empty()  # tpyc: error(/Cannot infer type arguments/)

def main():
    pass
