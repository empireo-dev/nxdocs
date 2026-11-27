# resdb — the resource manifest

`resdb` is the game's **resource manifest**: a single file listing the virtual
path of every resource the client ships. This document describes what it is for,
the container it is stored in, and the exact layout of its rows — enough to read an
existing `resdb` or write a new one.

Figures below (sizes, row counts) are taken from the shipped and patched manifests
of one PC installation; the format itself is fixed.

## 1. Why it exists

The NeoX engine keeps its resources in **NXPK archives** that are keyed by a hash
of each file's path — the archives store no file names, only a 32-bit hash per
entry. That is enough for the engine to **load** a resource whose path it already
knows (hash the path, look the hash up in the archive), but it leaves no way to
**enumerate** what exists or to turn a stored hash back into a readable path.

`resdb` fills that gap. It is the authoritative catalogue of resource paths — the
directory listing that the hash-keyed archives do not carry themselves. It is used
wherever the whole set of resources matters rather than one known path:

- **Patching / content updates.** The installer and the live patches each ship a
  manifest, so the client knows the full, current set of resource paths.
- **Name recovery.** Because the archives contain only hashes, the only way to
  recover an entry's path is to hash candidate paths from `resdb` and match them
  against the archive's entries (see §4).

`resdb` lists **resources only** — textures, meshes, materials, audio, and so on.
It does not list game **scripts** (those live in their own archives and are not in
the manifest), and it never lists the archive files themselves.

### The two manifests

A normal installation contains two `resdb` files that share the same format:

| Path | Role | Rows (observed) |
|---|---|---|
| `res/resdb` | Shipped with the installer | 413,650 |
| `Documents/res/resdb` | Patched: updated by live content patches, newer and larger | 453,876 |

(Exact counts are from one installation and grow as patches add resources.)

They describe one and the same resource tree. A consumer typically reads both and
uses the **union** of their paths, so that patched entries are covered.

## 2. Container format (cocos2d-x CCZ)

A `resdb` file is stored in the **cocos2d-x CCZ** compressed-file format: a 16-byte
header followed by a compressed body. All integer fields in the header are
**big-endian** (unlike the little-endian NXPK archives).

| Offset | Size | Field | Value in `resdb` |
|---|---|---|---|
| `0x00` | 4 | Signature, ASCII `CCZ!` (`43 43 5A 21`) | `CCZ!` |
| `0x04` | 2 | Compression type, `uint16` — `0` selects zlib | `0` (zlib) |
| `0x06` | 2 | Format version, `uint16` | `2` |
| `0x08` | 4 | Reserved, `uint32` | `0` |
| `0x0C` | 4 | Uncompressed size in bytes, `uint32` | e.g. `26676155` |

The body, from offset `0x10` to the end of the file, is a single standard **zlib
stream** (it begins `78 9C`). Inflating it yields the payload described in §3; its
length equals the header's uncompressed-size field.

A real header, annotated:

```
43 43 5A 21   "CCZ!"            signature
00 00         0                 compression type = zlib
00 02         2                 version
00 00 00 00   0                 reserved
01 97 0B BB   26,676,155        uncompressed size (big-endian)
```

**Reading:** confirm the first four bytes are `CCZ!`, then zlib-inflate everything
from offset 16 onward.

**Writing:** zlib-compress the payload text, then prepend the 16-byte header with
compression type `0`, version `2`, reserved `0`, and the payload's byte length in
the big-endian size field.

## 3. Payload: the row list

The inflated payload is a **plain-text listing**, UTF-8 encoded, with `\n` (LF) line
endings and a trailing newline. Each line is one resource:

```
<flag> <path>
```

- **flag** — a leading integer field. It is `0` for every row in both the shipped
  and patched manifests; its purpose is not exposed and consumers ignore it.
- a single ASCII space, then
- **path** — the resource's virtual path, using backslash (`\`) separators. Paths
  are stored as-is; in the observed manifests they are entirely lowercase.

Example rows:

```
0 animators\aoluodaoshi_luoen_aoluo.animgraph
0 model\character\appearance\girl\low\...\female_hair_chaifen_02.mesh
```

Properties of the list:

- **Unique.** No path appears twice within a file.
- **Unordered.** Rows are grouped loosely by top-level folder but are **not** in any
  global sort order, and the on-disk order carries no meaning. A consumer that needs
  sorted or prefix lookups sorts the list itself after loading.

## 4. How a path maps to an archive entry

`resdb` stores **full paths** in the one virtual resource tree. An archive, however,
keys each entry by the hash of its path **relative to where that archive is mounted**
in the tree. Connecting the two is therefore:

1. Take the archive's mount prefix (the subtree it provides — e.g. `ui\` for the UI
   archive, empty for the tree-root archive).
2. From each `resdb` path under that prefix, strip the prefix to get the path
   *inside* the archive.
3. Hash that inner path and match it against the archive's entry hashes.

The hash is **32-bit MurmurHash3 (x86 variant) with seed `0x9747B28C`**, computed
over the inner path as UTF-8 bytes with backslash separators and original case — the
same hash the archive uses as the entry's key. Because the hash is one-way,
matching candidate paths from `resdb` is the only way to turn a stored hash back
into a readable name — which is why the archives depend on `resdb` for naming. The
mount-prefix rules themselves belong to the archive-mounting scheme and are outside
this document.

## See also

- `npk.md` — the NXPK archive format that `resdb` names, including the entry hash.
