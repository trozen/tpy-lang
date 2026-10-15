# A cycle member's field `peer.Item | None` holds the Item inside the class, so
# it is rejected like a by-value field (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
