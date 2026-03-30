from tpy import Int32

__all__ = ["public_fn"]

def public_fn(x: Int32) -> Int32:
    return x

def hidden_fn(x: Int32) -> Int32:
    return x + Int32(1)
