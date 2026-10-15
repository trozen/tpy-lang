# A cycle member's field may not name a generic class over a peer's class, even
# one holding it through a `Ptr` (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
