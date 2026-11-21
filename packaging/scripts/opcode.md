# Opcode de-obfuscation

How the game's compiled Python scripts encode (obfuscate) their bytecode opcodes, and how to decode them back to stock CPython 2.7.3 so an ordinary decompiler can read them.

This document is the reference; the decoding data lives in four CSV tables
beside it (`opindex`, `opdirect`, `opfused`, `opprev`, described in
[§4](#4-the-tables)). The Python build itself — version, how it is linked, how
it was identified — is covered separately in [`python.md`](../../python.md);
this document assumes it and does not repeat it.

Two terms are used throughout:

- **encoded byte** — a raw opcode byte as it appears in a shipped script.
- **stock opcode** — the standard CPython 2.7.3 opcode it decodes to.

---

## 1. Background: opcodes in CPython 2.7.3 bytecode

A compiled Python module is a tree of *code objects* (one per module, function,
class body, comprehension, …). Each code object carries, among other fields, a
byte string `co_code` — the instruction stream — plus side tables the
instructions index into: `co_consts` (literals), `co_names` (global/attribute
names), `co_varnames` (locals), and so on.

**Instruction format.** Each instruction is one opcode byte, optionally followed
by a two-byte operand:

- If the opcode is **below `0x5A` (90)** it has **no operand** and the
  instruction is **1 byte** long.
- If the opcode is **`0x5A` or above** it has a **two-byte operand** and the
  instruction is **3 bytes** long.

That cutoff (`HAVE_ARGUMENT = 0x5A`) is a fixed property of the opcode value, so
an instruction's length is known from its opcode byte alone. This matters because
decoding is a linear walk: after each instruction you advance by its length to
reach the next opcode. Misjudge one length and the walk desyncs — you start
reading an operand byte as an opcode and everything after it is garbage.

**The operand** is a single 16-bit value stored little-endian: low byte first,
high byte second. It is *one* argument, not two; a small value simply leaves the
high byte `0x00`. Its meaning depends on the opcode — an index into `co_consts` /
`co_names` / `co_varnames`, a jump target, or a packed count (for example
`CALL_FUNCTION` packs the positional-argument count in the low byte and the
keyword-argument count in the high byte, still within the one 16-bit operand).

**EXTENDED_ARG.** Because operands are only 16 bits, a value above 65535 (a const
index or jump target in a very large code object) cannot fit. CPython handles
this with an `EXTENDED_ARG` instruction that carries the upper bits and prefixes
the real instruction; the interpreter forms the effective operand as
`(extended_arg << 16) | operand`. It is rare but must be recognised, because it
occupies its own 3 bytes and alters how the following instruction's operand is
read.

**What the obfuscation touches.** Only the *opcode bytes* are transformed.
Operand values, the `co_consts` / `co_names` / `co_varnames` tables, and the
overall marshal/code-object structure are untouched. So once the opcode bytes are
decoded, the result is ordinary stock 2.7 bytecode that a standard decompiler
reads directly.

---

## 2. The obfuscation

### Two stages: compiler and packer

The encoding is produced in two stages, by two different pieces of software:

1. **Direct permutation** — applied by the game's **modified Python compiler**
   when source is compiled to bytecode. It is all the compiler does: the
   standalone `python.exe` emits exactly this and nothing more. It is *direct
   substitution* below.
2. **Fusion** — applied by a **separate packing tool** over the already-permuted
   bytecode; the compiler never produces it. The packer folds common runs of two
   or three consecutive opcodes into single *superinstruction* bytes. It comes
   **after** direct substitution (it operates on the compiler's canonical bytes).

There is **no alias layer.** The permutation is a bijection — exactly one byte per
stock opcode, and no opcode has a second standalone encoding. What flattens the
opcode frequency is fusion, not alternate names (see the anti-frequency note
below).

The **modified interpreter executes all of it directly**: its evaluation loop has
handlers for the permuted opcodes and for every superinstruction byte. There is no
de-obfuscation step at load time — the obfuscated form *is* the executed form. Our
decoding is the reverse direction, which exists nowhere in the game.

### The substitution layers

#### 1) Direct substitution — one byte to one opcode

The compiler renumbers each stock opcode to a single encoded byte — a fixed
**bijection**, one byte per opcode (no opcode has two encodings). The `0x5A`
length threshold is honoured by the permutation (a no-operand opcode maps below
`0x5A`, an operand-carrying one at or above it), so an instruction's length stays
readable from its encoded byte. This layer accounts for **118 bytes**, one per
stock opcode exercised, scattered across the byte space. `EXTENDED_ARG` (byte
`0xA0`) is an ordinary direct entry.

#### 2) Fusion — one byte to a short opcode sequence

The packer folds a run of two or three consecutive stock opcodes into a single
*superinstruction*. There are two forms, distinguished by how many 16-bit operands
the folded run must carry:

**a) Pure fused.** The run needs **at most one** stream operand, so a single
encoded byte suffices (3-byte if it carries that operand, 1-byte if the run is
operand-free). **14** are catalogued, scattered `0x12`–`0xAD`. Each sub-opcode's
operand source is recorded per sub-op in `arg` (see [§4](#4-the-tables)): the one
stream operand, a fixed constant, or none. Exactly one sub-opcode takes a fixed
implicit operand anywhere in the scheme — `0xAD = LOAD_FAST ; LOAD_CONST`, whose
`LOAD_FAST` always loads local `0` (`arg = c0;0`).

**b) `0xB2` combos.** When the folded run needs **two or more** stream operands,
one encoded byte cannot carry them all. The packer emits the head byte to carry
the first operand and parks each *additional* operand in its own 3-byte slot whose
opcode position is filled with the inert magic byte **`0xB2`** — so a combo is a
run like `[head][op0] [0xB2][op1] [0xB2][op2]…`. **The head byte of a combo never
appears alone:** it is *always* immediately followed by at least one `0xB2` slot,
and it always runs its whole sequence. **30** combos are catalogued, keyed by the
head byte in `opprev`; the head byte alone determines the entire expansion — which
is why `0xB2`'s meaning *looks* like it depends on the "previous byte," when in
fact the head's handler simply reads the operand sitting in the `0xB2` slot.
**`0xB2` is never a standalone opcode and decodes to nothing**; it is pure filler
marking an extra-operand slot, and its value is never inspected.

**Anti-frequency, correctly stated.** A plain permutation is a substitution cipher
that frequency analysis would break — real bytecode is dominated by a handful of
opcodes, so the commonest byte is almost certainly one of them. Fusion defeats
this *without* giving any opcode a second name: a hot opcode such as `LOAD_FAST`
is reached through its own canonical byte **and** as a sub-op of many combos
(`0x7f, 0x8f, 0x90, 0xa4, 0xa5, 0xa7, 0xa8, …`), so its work is spread across many
different encoded bytes and no single byte dominates the histogram. There is still
exactly one byte per standalone opcode; the same operation merely *also* appears
inside combos.

### How the interpreter de-obfuscates — the switch

The evaluation loop (`PyEval_EvalFrameEx`, statically compiled into the game
binaries) is one large switch over the encoded opcode byte, and it is where every
layer is undone at run time:

- **Direct bytes** are simply the case labels — the switch is renumbered to the
  permuted opcodes. (The compiler's code generator and the interpreter's switch
  were regenerated from one rewritten opcode header, so the two agree by
  construction.)
- **Pure-fused bytes** have dedicated cases that execute the whole short sequence,
  reading the single operand if the byte carries one.
- **Combo heads** have dedicated cases too: the handler runs the first sub-op with
  the head's own operand, then reads the operand parked in the following `0xB2`
  slot, runs the next sub-op, and advances over the `0xB2` byte — never inspecting
  its value. Longer combos repeat this for each extra `0xB2` slot.
- **`0xB2` has no case at all.** It lies above the switch's case bound and is never
  dispatched, because the preceding combo head always consumes its slot. (A `0xB2`
  that somehow reached the switch would fall to the default and raise "unknown
  opcode".)

The `HAVE_ARGUMENT` test that decides whether to fetch a two-byte operand is
unchanged.

---

## 3. Decoding procedure

Walk `co_code` from left to right. At each position:

1. **Read** the encoded byte and determine its length (from the `0x5A`
   threshold, or equivalently from `opindex`).
2. **Resolve by role:** *direct* → emit the stock opcode; *pure fused* → emit the
   sub-opcode sequence; *combo head* → emit its whole sequence, taking each later
   sub-op's operand from the `0xB2` slot(s) that immediately follow. A bare `0xB2`
   is never decoded on its own — the combo head before it has already consumed its
   slot.
3. **Route the operand** from each sub-opcode's `arg` entry — a stream-operand
   slot, a fixed constant (`cN`), or none — and fold any `EXTENDED_ARG` into the
   following instruction's operand. No operand is ever split across sub-opcodes.
4. **Advance** by the instruction's length and repeat.

Recurse into nested code objects found in `co_consts`. The result is stock
CPython 2.7 bytecode suitable for a standard decompiler.

---

## 4. The tables

Four CSV files sit beside this document. They are **built by a tool** (`opmerge`)
that merges the per-investigation tables under `opcode-tools/`; the three decode
tables hold only what is **resolved**, and `opindex` is the coverage map over all
256 bytes. Shared columns: `*_hex`/`*_dec` give a byte both ways, `name` is the
stock opcode, `has_arg` whether it takes an operand, and `source` names the
investigation(s) a row came from (`oracle`, `stdlib`, later `switch`).

| File           | One row per                | Key columns                                                       | Purpose                                                                                     |
| -------------- | -------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `opdirect.csv` | direct byte                | `perm_hex, perm_dec, stock_hex, stock_dec, name, has_arg, source` | The byte → stock-opcode map (a bijection; no alias column — there are no aliases). |
| `opfused.csv`  | pure-fused byte            | `perm_hex, perm_dec, expands_to, stock_hex_list, arg, source`    | Each byte's stock-opcode sequence (≤1 stream operand); `arg` is the per-sub-op operand-source list. |
| `opprev.csv`   | `0xB2` combo               | `prev_hex, prev_dec, expands_to, stock_hex_list, arg, source`     | The whole sequence a **combo head** runs, keyed by that head byte (which is always followed by the `0xB2` magic byte); `arg` is the per-sub-op operand-source list. |

`arg` is a `;`-list with one entry per sub-opcode in `expands_to`, saying where
that sub-op's 16-bit operand comes from: a **stream slot** (`0` = the byte's own
operand, `1`/`2` = later `0xB2`-slot operands), `cN` = a **fixed constant**, or
`-1` = **no operand**. (Only `0xAD`'s `LOAD_FAST` uses a constant, `c0`; no operand
is ever split across sub-ops.) This makes operand routing exact for a
de-obfuscator.
| `opindex.csv`  | every byte `0x00`–`0xFF`   | `byte_hex, byte_dec, role, status, decode, source`               | Routing/coverage map: `role` (`direct`/`fused`/`combo-head`/`combo-filler`) and `status`.   |

`opprev` has the same shape as `opfused`; the one difference is that a combo's
later operands ride in mandatory `0xB2` continuation slots, so its head byte never
occurs standalone. In `opindex`, `status` is `resolved`, `unknown` (not yet decoded
but within the used range — for the switch), or `unused` (above the last used byte,
where the switch has no case).

Current coverage: **163** of 256 bytes resolved — **118** direct, **14** pure-fused,
**30** `0xB2` combo heads, and the `0xB2` filler byte itself. Of the rest, **16**
are `unknown` (in-range, still to recover) and **77** are `unused` (the
`0xB3`–`0xFF` block). Every resolved row is from a ground-truth source (§5), so the
tables carry no `tentative`/`draft` state.

---

## 5. Authoritative sources

How each layer's table is established. Every entry comes from a ground-truth
source — the interpreter's own compiler output or its packed stdlib — never from
statistics. Two methods cover the layers.

### Direct substitution — the oracle method

The compiler is its own oracle. Compile known source with **both** the game's
`python.exe` (which emits the permuted encoding) and a stock Python 2.7.3 (which
emits standard bytecode). Because direct substitution preserves instruction
structure, the two streams line up one instruction at a time; at each position the
stock opcode names the permuted byte, and the operands must match the whole way
through — the built-in correctness check. A corpus that exercises every opcode
yields the direct table deterministically. The compiler never emits fused bytes
or combos, so this recovers **only** the direct bytes.
Implemented by [`opcode-tools/opcex-oracle`](../../../opcode-tools/opcex-oracle).

### Fusion and combos — the stdlib known-plaintext method

The packer's fusion never appears in the compiler's output, so the oracle is
blind to it. Instead recover it from the game's own **packed stdlib**, whose
source we know (stock 2.7.3) — a known-plaintext attack. For each module, compile
the source to stock bytecode (the *plaintext*) and read the packed `.pyc` (the
*ciphertext*), then align the two per code object:

- **Pair by signature, not position.** The game ships a *patched* stdlib (NetEase
  backported later 2.7.x fixes), so some functions differ. Code objects are paired
  by signature (name, argcount, locals, names, stacksize, consts — all untouched
  by packing); version-skewed functions are skipped.
- **Align each object whole, by dynamic programming.** The stock side is fully
  known, so for a correctly-paired object exactly one width-assignment is
  consistent with every operand. The DP tries each operand-consistent width for
  every unknown byte and asks only whether a complete path exists. A byte is read
  off **only where its meaning is identical across every consistent path** (proven
  by the operands); a pure-fused byte maps to a short run needing ≤1 operand, while
  a **combo head** is recognised because it is immediately followed by the `0xB2`
  magic byte — the head plus its `0xB2` slot(s) are read off together as one combo
  (the operands revealing which sub-op receives each). An object with **no**
  consistent path is proven misaligned and rejected, and the pass restarts without
  it — so a mismatch can never leak a wrong mapping.
- **Corroborate.** A reading is accepted only when **≥2 independent objects agree**;
  a disagreement between two corroborated readings is a hard stop. The result is
  deterministic with zero conflicts.

This yields the pure-fused expansions (`opfused`) and the `0xB2` combos
(`opprev`), each keyed by its head byte with full per-sub-op operand routing; it
finds **no** new standalone direct bytes (every byte it resolves to a single op is
really a combo head, so it goes to `opprev`, not `opdirect`). Implemented by
[`opcode-tools/opcex-stdlib`](../../../opcode-tools/opcex-stdlib). Bytes the stdlib
never exercises unambiguously stay `unknown`, to be recovered from the interpreter
switch.
