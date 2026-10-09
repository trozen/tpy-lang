# tpy: cpp_namespace("tpystd::types")
"""Minimal `types` module -- `FrameType` only.

`FrameType` is here so a `signal.signal` handler is annotated as in CPython,
`def on_term(signum: int, frame: FrameType | None) -> None`. TPy has no frame
objects: a handler always receives `None`, where CPython passes the
interrupted frame. Under CPython `import types` resolves to the real stdlib
module.
"""


class FrameType:
    """An execution frame. Opaque: TPy never creates one."""

    def __init__(self) -> None:
        raise TypeError("cannot create 'frame' instances")
