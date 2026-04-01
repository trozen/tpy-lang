# Panic with expression message: assert False with variable message.

def fail(msg: str) -> None:
    assert False, msg

fail("dynamic message")
