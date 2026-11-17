# `ra` binds E by an import only after it starts `rb`.
import rb
from other import E  # noqa: E402
