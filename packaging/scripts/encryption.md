# Script encryption

The game's scripts ship **encrypted**. This document describes that encryption —
the wrapper around each compiled script — and how it is undone. It is the **first
layer of defence** protecting the scripts: it must be stripped before any of a
script's contents can be read.

Scripts are bundled together in their own package, `script.npk`. Unpacking that
container is a separate matter (handled by the asset tooling) and is **not** covered
here — this document assumes you have already extracted one raw script entry and
deals only with decrypting it.

---

## 1. What is wrapped

The protected payload is a single **marshalled Python 2.7 code object** — a
compiled module exactly as `marshal.dumps` produces it. The encryption layer treats
it as an opaque byte string; it neither knows nor cares what the bytes are.

## 2. How the wrapper is built

To ship a script the engine applies four transforms to the marshal bytes, in this
order:

1. **Reverse** the whole byte string.
2. **Mask** — XOR the **first 128 bytes** (of the reversed string) with the
   constant **`0x9A`**; leave the rest untouched.
3. **Compress** with **zlib**.
4. **Encrypt** with the **`rotor`** cipher (§3) under the game key (§4).

The result is stored as an entry in `script.npk`.

Decoding reverses the four steps exactly — rotor and zlib are true inverses, and
the reverse and the `0x9A` mask are each **their own inverse**:

```
rotor.decrypt  ->  zlib.inflate  ->  XOR-first-128 (0x9A)  ->  reverse  ->  marshal bytes
```

**Recognising a wrapped entry.** A wrapped script begins with one of two fixed
two-byte markers — `1D 04` or `15 23` — the encrypted leading bytes of the stream.
The tooling uses them to tell a wrapped script from the one script that is *not*
wrapped: the bootstrap `redirect.nxs` (it has to run before the decryptor exists),
which is plain marshal and begins with `0x63` (`TYPE_CODE`).

The reverse + `0x9A` mask add nothing cryptographically — they are light
obfuscation to stop a casual `zlib`/`marshal` scan from recognising the payload.
The real work is the rotor cipher.

## 3. The rotor cipher

`rotor` is Andrew Kuchling's old Enigma-style rotor machine. It shipped with
CPython 2 until it was removed in 2.4; **NeoX re-added it** to its embedded Python
2.7 specifically to encrypt scripts. HPMA uses **6 rotors over a 256-symbol
alphabet** (one symbol per byte).

How it works, from the key:

- **Key schedule.** Three 16-bit [Wichmann–Hill](https://en.wikipedia.org/wiki/Wichmann%E2%80%93Hill)
  generators are seeded from the key: for each key byte, each of the three seeds is
  rotated left by 3 bits and then combined with the byte (one by `+`, one by `^`,
  one by `-`). The middle seed is forced odd, and one overflow-safe step is run so
  the first draw is well mixed. This gives a key-dependent stream of pseudo-random
  numbers.
- **Build the rotors.** Each of the 6 rotors gets, from that stream: a random
  **start position**, an odd **advance step**, and a random permutation `E` of the
  256 symbols (Fisher–Yates) together with its inverse `D`.
- **Encrypt a byte.** Run it forward through all 6 rotors (XOR with the rotor's
  position, then substitute through `E`), emit the result, then **step the rotors
  like an odometer**: rotor 0 advances by its step, carrying into rotor 1, and so
  on. Decryption is the same with the inverse permutations `D` run in reverse rotor
  order. Because the rotors step after every byte, the cipher is stateful — the same
  plaintext byte encrypts differently at each position.

The odometer carry is the fiddly detail (a rotor's new position is
`((pos + carry_in) & 0xFF) + step` taken mod 256, carrying when that sum reaches
256 *before* the modulo). Getting it wrong only desyncs after the first wraparound,
so a near-miss implementation still fails — which is a useful correctness signal.

The algorithm was **recovered from the engine binary** (the rotor C functions in
`launcher.exe`) and cross-checked against the reference `rotor` port in NeoXtractor.

## 4. The key

The cipher is standard; the one game-specific secret is the key. It is **297 bytes**,
assembled from three short string fragments:

```
dn = "j2h56ogodh3se"
dt = "=dziaq."
df = '|os=5v7!"-234'

key = dn*4 + (dt + dn + df)*5 + "!#" + dt*7 + df*2 + "*&'"      # 297 bytes
```

(The fragments are plain ASCII; the multiplications and the literal `!#` / `*&'`
separators are exactly the expression the engine uses.)

## 5. Where the key was found

Nothing clever was needed: the three fragments are plain ASCII text, and they were
found **early, by browsing the strings** in the data — they sit there in the clear.

They belong to a small setup function, `init_rotor()`, in the bootstrap script
`redirect.nxs` (the loader that switches on script decryption). That script cannot
be encrypted — it has to run before the decryptor exists — so it is not
rotor-wrapped and its text reads straight out, the fragments included. How the three
combine into the 297-byte key is the expression in §4.

## 6. Reference implementation

A self-contained reference decryptor sits beside this document:
[`decrypt.py`](decrypt.py). In one dependency-free file it implements the rotor
cipher and key (§3–§4) and the four-step unwrap (§2), exposed as
`unwrap(entry_bytes) -> marshal_bytes` plus a small command-line entry point. It is
processing logic only — the one game-specific value is the key, which can be
overridden.
