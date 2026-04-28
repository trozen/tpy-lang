# hashlib SHA-256: known FIPS 180-4 test vectors + streaming + copy.
# Only SHA-256 shipped; MD5/SHA-1/SHA-512 follow-up.
from hashlib import sha256

def main() -> None:
    # NIST FIPS 180-4 vectors.
    h0 = sha256(b"")
    print(h0.hexdigest())
    h1 = sha256(b"abc")
    print(h1.hexdigest())
    h2 = sha256(b"The quick brown fox jumps over the lazy dog")
    print(h2.hexdigest())
    # 56-byte input: forces the length-field into a second final block.
    h3 = sha256(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")
    print(h3.hexdigest())
    # Multi-block: 1000 'a' bytes spans 16 blocks.
    h4 = sha256(b"a" * 1000)
    print(h4.hexdigest())

    # Streaming: incremental update must match one-shot.
    s = sha256()
    s.update(b"abc")
    print(s.hexdigest())
    # Split input across multiple updates.
    s2 = sha256()
    s2.update(b"The quick brown fox ")
    s2.update(b"jumps over the lazy dog")
    print(s2.hexdigest())

    # digest() is idempotent -- second call returns the same bytes.
    s3 = sha256(b"abc")
    print(s3.hexdigest())
    print(s3.hexdigest())

    # copy() snapshots state: diverging the clone must not affect the original.
    base = sha256(b"hello")
    branch = base.copy()
    base.update(b" world")
    branch.update(b" there")
    print(base.hexdigest())
    print(branch.hexdigest())

    # raw .digest() length.
    print(len(sha256(b"abc").digest()))

    # Introspection.
    print(s3.digest_size)
    print(s3.block_size)
    print(s3.name)

main()
