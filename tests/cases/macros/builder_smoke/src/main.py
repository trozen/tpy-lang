# Smoke test for builder-trace macros (Phase 7 infrastructure).
# Exercises ctor + builder_method + builder_terminal end-to-end via a
# trivial Config builder defined in _smoke_builder.py.
from tpy import Int32
from _smoke_builder import Config


def main() -> Int32:
    cfg_builder = Config()
    cfg_builder.add("host", "localhost")
    cfg_builder.add("port", "8080")
    cfg = cfg_builder.build()
    print(cfg.host)
    print(cfg.port)
    return 0


main()
