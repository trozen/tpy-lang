from tpy import int32

__all__ = ["public_fn"]

def public_fn(x: int32) -> int32:
    return x

def hidden_fn(x: int32) -> int32:
    return x + int32(1)
