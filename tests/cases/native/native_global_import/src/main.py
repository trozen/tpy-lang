# Cross-module native_global import: the use site must emit the rename/binding
# stored on the source module's declaration, not `<consumer_ns>::<python_name>`.
# Covers both `from m import X` (Name reference path in codegen) and
# `import m; m.X` (module.var attribute access path).
import native_stub
from native_stub import score, lives, frame_count, tick

def main() -> None:
    # Name reference path (`from native_stub import X`)
    print(score)
    print(lives)
    print(frame_count)
    print(tick)
    # module.var access path (`import native_stub; native_stub.X`)
    print(native_stub.score)
    print(native_stub.lives)
    print(native_stub.frame_count)
    print(native_stub.tick)

main()
