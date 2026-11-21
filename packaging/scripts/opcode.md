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

There is **no alias layer.** The permutation is one-to-one — exactly one byte per
stock opcode, and no opcode has a second standalone encoding. What flattens the
opcode frequency is fusion, not alternate names (see the anti-frequency note
below).

The **modified interpreter executes all of it directly**: its evaluation loop has
handlers for the permuted opcodes and for every superinstruction byte. There is no
de-obfuscation step at load time — the obfuscated form *is* the executed form. Our
decoding is the reverse direction, which exists nowhere in the game.

### The substitution layers

#### 1) Direct substitution — one byte to one opcode

The compiler renumbers each stock opcode to a single encoded byte — a fixed,
**one-to-one** permutation, one byte per opcode (no opcode has two encodings). The
`0x5A` length threshold is honoured by the permutation (a no-operand opcode maps below
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

| File           | One row per              | Key columns                                                       | Purpose                                                                                                                                                    |
| -------------- | ------------------------ | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `opdirect.csv` | direct byte              | `perm_hex, perm_dec, stock_hex, stock_dec, name, has_arg, source` | The byte → stock-opcode map (one-to-one; no alias column — there are no aliases).                                                                          |
| `opfused.csv`  | pure-fused byte          | `perm_hex, perm_dec, expands_to, stock_hex_list, arg, source`     | Each byte's stock-opcode sequence (≤1 stream operand); `arg` is the per-sub-op operand-source list.                                                        |
| `opprev.csv`   | `0xB2` combo             | `prev_hex, prev_dec, expands_to, stock_hex_list, arg, source`     | The whole sequence a **combo head** runs, keyed by that head byte (always followed by the `0xB2` magic byte); `arg` is the per-sub-op operand-source list. |
| `opindex.csv`  | every byte `0x00`–`0xFF` | `byte_hex, byte_dec, role, status, decode, source`                | Coverage map: `role` (`direct`/`fused`/`combo-head`/`combo-filler`) and `status`.                                                                          |

`arg` is a `;`-list with one entry per sub-opcode in `expands_to`, saying where
that sub-op's 16-bit operand comes from: a **stream slot** (`0` = the byte's own
operand, `1`/`2` = later `0xB2`-slot operands), `cN` = a **fixed constant**, or
`-1` = **no operand**. (Only `0xAD`'s `LOAD_FAST` uses a constant, `c0`; no operand
is ever split across sub-ops — routing is exact for a de-obfuscator.) `opprev` has
the same shape as `opfused`; the one difference is that a combo's later operands
ride in mandatory `0xB2` slots, so its head byte never occurs standalone.

In `opindex`, `status` is just **`resolved`** or **`unused`** — there are no
`unknown` bytes. Reading the interpreter's own switch (§5) gave the complete set of
handled opcodes, and that set is exactly the resolved set, so every other byte has
no case and is unused (an in-range gap that falls to the switch's default/error, or
a byte above the last used value `0xB2`).

Current coverage: **163** of 256 bytes resolved — **118** direct, **14** pure-fused,
**30** `0xB2` combo heads, and the `0xB2` filler byte — and **93** unused (16
in-range gaps + the `0xB3`–`0xFF` block). Every resolved row is from a ground-truth
source (§5).

---

## 5. Authoritative sources

How the tables are established. Every entry comes from a ground-truth source, never
from statistics. **Three** sources cover everything: the compiler **oracle** (the
direct bytes), the packed **stdlib** (fusion), and the interpreter **switch** (the
few bytes the other two miss, plus the confirmation that the set is complete).

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

### Fusion — the packed-stdlib method

Fusion is added by the packer, not the compiler, so the oracle never sees it. But
the game ships its **own Python standard library** as packed `.pyc` files, and we
have the matching **original source** (stock 2.7.3). That is a known-plaintext
attack: compile the source ourselves to get the bytecode the module *should* have,
then line it up against the game's packed bytecode and read off what each encoded
byte means.

This is done one code object (module, function, …) at a time. For each we have two
instruction streams for the same program: the **stock** stream we compiled (fully
known), and the **packed** stream from the game (same logic, but opcodes permuted
and some runs fused into single bytes).

Fusion makes the packed stream *shorter*, and we don't know in advance which packed
byte is a plain opcode and which expands into two or three — so we let the
**operands decide**. Walk both streams from the start; at each packed byte the only
readings allowed are those whose operand matches the stock operand it lines up
against. A plain byte consumes one stock opcode, a fused byte consumes two or three,
and a byte followed by the `0xB2` magic byte is a combo read together with its
`0xB2` slot(s). Because the stock side is fixed, only one way of splitting the
packed stream fits every operand across the whole object — and that alignment tells
us what each byte expands to and which sub-op takes which operand.

Two things keep it honest:

- **Patched functions are skipped.** The game's stdlib is lightly patched (NetEase
  backported a few later 2.7.x fixes), so some functions no longer match our source
  and simply fail to line up. We drop those rather than force a wrong reading.
- **Every mapping needs corroboration.** A byte's meaning is accepted only once at
  least two independent functions agree on it; a real disagreement stops the run for
  inspection. The result is deterministic, with no conflicts.

This produces the pure-fused table (`opfused`) and the `0xB2`-combo table
(`opprev`), each with full operand routing. Implemented by
[`opcode-tools/opcex-stdlib`](../../../opcode-tools/opcex-stdlib). Opcodes the
stdlib never exercises are left to the switch.

### The interpreter switch — the last opcodes, and the completeness check

The third source is the interpreter itself. Its eval loop (`PyEval_EvalFrameEx`) is
one big switch over the encoded opcode byte, compiled into `launcher.exe`; we read
it in Ghidra. It is both a *source* for the few opcodes the other two miss and the
*proof* that the tables are complete. In short:

1. **Detangle.** Ghidra strips names, so the decompiled function's variables were
   renamed toward stock CPython 2.7.3 `ceval` names (`next_instr`, `oparg`, the
   frame and stack pointers, struct offsets) — giving a readable switch whose case
   labels are the encoded bytes.
2. **Label the known cases** from the oracle/stdlib tables, which leaves only a
   handful of blank cases.
3. **Body-match the rest.** The game loop and stock `ceval` are the *same* function,
   so a blank case is identified by *what its handler does*. This recovered the
   direct ops the oracle corpus never exercised (`NOP`, `ROT_FOUR`, `PRINT_EXPR`)
   and the fused/combo bytes the stdlib never used (`0x7a`, `0x98`).
4. **Confirm completeness.** The switch's jump table lists exactly which bytes have
   a handler — **162** opcodes — and that set is byte-for-byte identical to the
   union of the three tables (118 + 14 + 30). So every handled case is accounted
   for, no combo is missing, and every remaining byte has no case (hence `unused`).

Implemented and written up in
[`opcode-tools/opcex-switch`](../../../opcode-tools/opcex-switch) (the combo-specific
reasoning is in its `B2_COMBOS.md`).
