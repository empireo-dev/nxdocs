# NPK resource archives

How *Harry Potter: Magic Awakened* (NetEase NeoX engine) packages its resources:
the **NXPK** archive format, the light obfuscation on each stored file, the hash
index that lets the engine fetch one file without unpacking the rest, and the
`resdb` manifest that recovers file names. The detail here is enough to both
unpack and rebuild an archive. Observed on the PC client, version 1.5.21.3.

Scripts ship in the same kind of archive (`script.npk`), but each script's payload
carries a further, script-specific encryption on top of this packaging; see
[script encryption](scripts/encryption.md).

## 1. The container (NXPK)

An `.npk` file is a NeoX **NXPK** archive: a fixed header, every stored file laid
out back to back, and a **table of contents** (TOC) at the end. All integers are
**little-endian unsigned 32-bit**.

Header (24 bytes, `0x00`–`0x17`):

| Offset | Size | Field |
|---|---|---|
| `0x00` | 4 | Magic `NXPK` |
| `0x04` | 4 | Entry count |
| `0x08` | 12 | Unused — zero in every archive seen |
| `0x14` | 4 | TOC offset |
| `0x18` | — | Stored file data begins here |

The TOC is `entry_count` rows of **28 bytes**, seven u32 fields each:

| Field | Meaning |
|---|---|
| `id` | Hash of the file's path inside the archive (§2) |
| `offset` | Where the stored bytes start in the `.npk` |
| `packed_size` | Length of the stored bytes |
| `raw_size` | Length of the original file |
| `crc_packed` | Hash of the stored bytes, exactly as on disk |
| `crc_raw` | Hash of the original file |
| `flags` | Low byte = compression mode; bit 16 = encrypted (§4–§5) |

The archive stores **no file names** — only the hash of each path. Rows are ordered
by `id` ascending (§3).

## 2. One hash, content addressing

NeoX identifies and checksums everything with **32-bit MurmurHash3 (x86 variant),
seed `0x9747B28C`**:

- **`id`** — hash of the file's path *inside the archive* (§8): UTF-8, `\`
  separators, original upper/lower case.
- **`crc_packed`** — hash of the stored (compressed, encrypted) bytes.
- **`crc_raw`** — hash of the original file.

Because the id is a hash of the path, the archive is a **content-addressed store**:
the engine needs no name table at runtime. It resolves a virtual path by hashing it
(relative to the archive's mount point) and looking the id up in the TOC. Names live
only in `resdb` (§8), for tools and enumeration.

## 3. Random access: fetching one entry without unpacking

The format is built for direct lookup, not sequential unpacking:

- **Directory separate from data.** The TOC is one contiguous block of fixed-width
  rows at a known offset (`0x14`). A single seek and read load the complete
  directory — every entry's location, size, flags and checksums — without touching
  any payload.
- **Sorted hash index.** Rows are in ascending `id` order, so a path is resolved by
  hashing it and **binary-searching** the TOC (O(log n)) — no linear scan, no name
  list.
- **Per-entry isolation.** A row gives `offset` and `packed_size`, so the engine
  seeks straight to the one file and reads exactly its bytes; each entry is
  independently compressed and (optionally) encrypted, so it decodes on its own (§6).
- **Cheap obfuscation.** Only the first 128 bytes of a stored file are scrambled
  (§4), so decoding a single entry costs little beyond its decompression.

`offset` is the storage order and need not match the TOC (id) order. A tool
unpacking everything reads entries **by `offset`** (front to back) for speed; a tool
fetching one file uses the **id** index.

## 4. Entry obfuscation

If an entry's `flags` has **bit 16** (`0x00010000`) set, the start of its stored
bytes is scrambled; otherwise the bytes are stored in the clear. The transform
touches only the **first 128 bytes**: byte `i` (for `i < 128`) is XORed with
`(6 + i)`, i.e. the fixed ramp `06 07 08 … 85`. Everything from byte 128 on is left
untouched.

The XOR is **its own inverse**, so one operation both encrypts and decrypts. This is
obfuscation, not encryption: it exists only to defeat generic NXPK tools, which fail
because a compressor's leading bytes (e.g. the LZ4 block header) no longer parse. (It
was recovered by **known-plaintext**: a known file header XORed against the stored
bytes yields the `06 07 08 …` ramp, and only a 128-byte span decodes cleanly.) Most
resource entries are encrypted LZ4 (`flags = 0x10002`); much Wwise audio is stored or
zlib-compressed with no encryption.

## 5. Compression

The **low byte** of `flags` selects the codec:

| Value | Mode | Notes |
|---|---|---|
| 0 | Stored | the bytes are the original |
| 1 | zlib | standard zlib stream |
| 2 | LZ4 | LZ4 **block** format, no size prefix; decompress with output length = `raw_size` |
| 3 | zstd | output length = `raw_size` |

`raw_size == 0` is an empty file (no stored bytes).

## 6. Decoding an entry

Reverse the stored form in four steps; the two hash checks make the result provably
identical to the original:

```
stored bytes
  → check crc_packed            (hash of the stored bytes)
  → decrypt   (only if bit 16)  (XOR the first 128 bytes, §4)
  → decompress (by flags, §5)   (to exactly raw_size bytes)
  → check crc_raw               (hash of the result)
  → original file
```

## 7. Building an archive (repacking)

Packing is the exact inverse, and the format carries everything a packer needs: a TOC
row is built from the file's bytes, its chosen `flags`, and its id — **no file name is
required**.

Per file:

1. `raw_size` = length of the original; `crc_raw` = hash of the original.
2. Compress by the chosen mode → compressed bytes (mode 0 leaves the original
   unchanged).
3. If encrypting, XOR the first 128 bytes with the `06…85` ramp (same op as decrypt)
   → **stored bytes**, and set bit 16 in `flags`.
4. `packed_size` = length of the stored bytes; `crc_packed` = hash of the stored bytes.
5. `flags` = compression mode (low byte) | encryption bit.
6. `id` = hash of the file's path inside the archive. A file whose name was never
   recovered already has a known id (its only identifier — see §8), so an unnamed file
   must keep its id and not be renamed.

Then assemble the file:

1. Lay the stored payloads out from `0x18`; each file's start is its `offset`.
2. Write the header: `NXPK`, the entry count, zero for `0x08`–`0x13`, and the TOC
   offset (the end of the payloads).
3. Write the TOC at that offset: one 28-byte row per entry, **sorted by `id`
   ascending** — what the loader's binary search relies on (§3).

Round-trip caveats:

- **Compressed bytes are not canonical.** A different zlib/LZ4/zstd build or level
  produces different — but valid — stored bytes, so `packed_size` and `crc_packed`
  will not match the originals. This matters only if something compares these against
  a value kept outside the archive's own TOC; within the archive the recomputed
  checksums are self-consistent.
- The unused header words (`0x08`–`0x13`) have been zero in every archive seen.
- For a byte-exact rebuild, preserve each entry's original `flags` (compression mode
  and encryption bit) and its TOC order.

## 8. File names: the `resdb` manifest

The archive holds only ids; names come from a separate manifest the game ships:
**`resdb`**.

### 8.1 How a name is stored in `resdb`

A `resdb` file is a **`CCZ!` container**: a 16-byte header whose first four bytes are
the magic `CCZ!` (the remaining 12 bytes are container metadata — sizes/version — and
are skipped), followed by a single **zlib** stream. The decompressed payload is UTF-8
**text, one line per resource**:

```
<flag> <path>
```

— a per-resource flag, a space, then the resource **path** with `\` separators and
original case (e.g. `ui\login\bg.png`). The flag is per-resource metadata not needed
for naming and can be ignored. `resdb` lists the **files inside archives only**: it
never lists the archives themselves, never stores ids, and omits scripts.

A normal installation has two manifests — `res/resdb` (shipped) and
`Documents/res/resdb` (patched) — whose path lists are **merged** (union).

### 8.2 How `resdb` is organized for naming

`resdb` is a flat list of **full paths in the one virtual resource tree**, not paths
per archive. An entry's `id`, however, is the hash of its path **relative to where its
archive is mounted** in that tree. Naming an archive is therefore a prefix-slice,
hash, and match:

1. Determine the archive's **mount prefix** — the subtree it fills:

   | Archive | Mount prefix | Example manifest path → inner path |
   |---|---|---|
   | `res.npk` (tree root) | *(none)* | `common\shader\x.xml` → `common\shader\x.xml` |
   | `res/ui.npk` | `ui\` | `ui\login\bg.png` → `login\bg.png` |
   | `res/model/character.npk` | `model\character\` | `model\character\f\body.mesh` → `f\body.mesh` |

2. Take every manifest path under that prefix and **strip the prefix** to get the
   inner path.
3. **Hash** the inner path (§2) → candidate id.
4. **Match** candidate ids against the archive's TOC ids; a hit names that entry.

The manifest is thus naturally used as a **prefix-searchable index**: sort the merged
paths case-insensitively once, and every mount prefix becomes a contiguous range
located by binary search for its two ends. (That ordering is the reader's; on disk `resdb` is
just the line list.) Several archives can mount at the same subtree (multiple `ui.npk`,
`character.npk`, split for size); they share one prefix table.

Entries with no matching path stay **unnamed**: the id is their only identity (keep it
when repacking), and a tool typically names them by id with an extension guessed from
the file's leading bytes. Coverage is roughly 97–100% for resource archives and ~0%
for scripts (not listed).

Which subtree a given archive mounts under is decided by the engine's **mount folders**
— a fixed list of resource roots, each loaded to a set depth. That mapping is an
installation-layout concern beyond this format note; what matters for naming is the
resulting **prefix** above.

## 9. Entry contents and the script layer

An unpacked entry is still in its **NeoX on-disk format** — DDS textures, `CMesh`
models, NeoX binary XML (materials, prefabs, scenes), Wwise `.wem`/`.bnk` audio, and so
on — which these steps do not convert. Script entries (`script.npk`) decode and verify
like any other entry, but what comes out is a script still wrapped by the
**script-encryption layer** (its payload begins `1D 04`). Decrypting it, unmarshalling
the Python 2.7 code object, and de-obfuscating the bytecode are the next layers,
described in [script encryption](scripts/encryption.md) and
[opcode obfuscation](scripts/opcode.md).
