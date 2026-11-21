# The Python the game uses

Short reference for the embedded Python runtime in *Harry Potter: Magic Awakened*
(NetEase NeoX engine, codename G92). Written 2026-11-20.

## Version

- **Python 2.7.3** — the interpreter's own `PY_VERSION`, compiled into the binary.
- **Build:** MSVC — `[MSC v.1929 64 bit (AMD64)]` → Visual Studio 2019 (16.11), 64-bit.
  NetEase rebuilt the 2.7.3 source with their own toolchain; it is **not** a
  stock python.org binary.
- **Marshal / .pyc:** stock CPython 2.7 format. Code objects begin with `0x63`
  (`'c'` = `TYPE_CODE`); the `.pyc` magic when wrapped is `03 f3 0d 0a`
  (constant across the whole 2.7 series, so it confirms "2.7" but not the micro —
  the micro comes from the `PY_VERSION` string, see below).

## Which executable

Python is **statically linked** into the NeoX binaries — there is **no**
`python27.dll` anywhere in the install. Two 64-bit (~64 MB) carriers:

| File                    | Role                                                                                                                                                                                     |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `neox_bin/launcher.exe` | The game client core. Contains the eval loop we disassembled (`FUN_141729f90` = the opcode switch). This is the authoritative binary for the opcode work.                                |
| `neox_bin/python.exe`   | A standalone build of the **same** embedded Python (same version, same modified interpreter). Can run a REPL / compile scripts — potentially usable as a ground-truth oracle under Wine. |

(`bin_version` = `22351` is the game build number, unrelated to the Python version.)

## Obfuscation

The interpreter is a fork, modified so it **runs obfuscated bytecode natively**:
there is no de-obfuscation step at load time — the obfuscated form *is* the
executed form. All of the obfuscation targets the **opcodes** (operand values and
the code-object structure are left untouched), in **four layers**:

1. **Direct substitution** — the opcode numbers are permuted.
2. **Alias substitution** — some opcodes gain several interchangeable encodings.
3. **Fusion** — common opcode pairs collapse into single bytes.
4. **Previous-dependent (`0xB2`)** — one byte whose meaning depends on the opcode before it.

The layers, the decoding rules, and the lookup tables are documented in full in
[opcode.md](packaging/scripts/opcode.md).

The interpreter can **run** all four layers but only **produces** the first: its
compiler emits the direct permutation, while a **separate packing tool** (not
shipped with the game) adds the alias, fusion, and `0xB2` layers on top. So
`python.exe` cannot repack a script into the shipped form — but it runs whatever
it compiles without trouble, which is all we need should we ever edit or write
new scripts.

## How to look it up again

```bash
cd "Installation/Magic Awakened/neox_bin"
# version (PY_VERSION, sits next to the Py_GetVersion format string):
grep -aoEh "2\.7\.3.{0,40}" python.exe | head
# compiler/build tag:
grep -aoEh "\[MSC v\.[0-9]+ [0-9]+ bit \([^)]*\)\]" launcher.exe
# confirm no separate python DLL (static linking):
find "../.." -iname "python*.dll"
```

Note: the binary also contains unrelated NetEase-SDK version strings
(`2.7.8` near `MPAY…`, `2.7.1` in `hhunter_mac/2.7.1@UniSDK/stable`) — those are
**not** the Python version. The real one is the `2.7.3` clustered with core
CPython strings (`%.80s (%.80s) %.80s`, `Python %s`, `f_back`, `f_code`).
