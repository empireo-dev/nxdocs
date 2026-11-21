#!/usr/bin/env python3
"""Reference decryptor for the HPMA script-encryption layer (see encryption.md).

Self-contained: no dependencies beyond the standard library, and no game data
except the key (which is a cipher parameter, overridable). Turns one *already
NPK-extracted* script entry into the Python-2.7 marshal bytes it wraps:

    marshal_bytes = unwrap(entry_bytes)

The wrapper the engine builds is, outermost first:

    rotor.encrypt( zlib.compress( xor_first_128( reverse( marshal_bytes ) ) ) )

so unwrapping reverses it:

    rotor.decrypt -> zlib.inflate -> xor_first_128 -> reverse -> marshal_bytes

`reverse` flips the whole byte order and `xor_first_128` flips byte i (i < 128)
with 0x9A; both are their own inverse.
"""
from __future__ import annotations

import sys
import zlib

# --- the key ------------------------------------------------------------------

# The three fragments init_rotor() holds, and the order it assembles them in.
_FRAGMENTS = ("j2h56ogodh3se", "=dziaq.", '|os=5v7!"-234')


def default_key() -> bytes:
    """The recovered HPMA script key (297 bytes)."""
    dn, dt, df = _FRAGMENTS
    key = dn * 4 + (dt + dn + df) * 5 + "!#" + dt * 7 + df * 2 + "*&'"
    return key.encode("latin-1")


# --- the rotor cipher ---------------------------------------------------------

class Rotor:
    """Andrew Kuchling's Enigma-style `rotor` machine (6 rotors, 256 symbols).

    `encrypt` and `decrypt` are exact inverses. The rotor tables depend only on
    the key, so they are built once; each call restarts the rotor positions.
    """

    def __init__(self, key: bytes, nrotors: int = 6, size: int = 256) -> None:
        self.key = bytes(key)
        self.nrotors = nrotors
        self.size = size
        self._forward = None   # E (forward permutations)
        self._reverse = None   # D (inverse permutations)
        self._start = None     # initial rotor positions

    def _seeded_rng(self):
        """A key-seeded Wichmann-Hill draw(n) -> int in [0, n)."""
        mask = 0xFFFF
        x, y, z = 995, 576, 767
        for byte in self.key:
            x = ((x << 3 | x >> 13) + byte) & mask
            y = ((y << 3 | y >> 13) ^ byte) & mask
            z = ((z << 3 | z >> 13) - byte) & mask
        half = mask >> 1
        if x > half:
            x -= mask + 1
        if y > half:
            y -= mask + 1
        if z > half:
            z -= mask + 1
        y |= 1
        # one overflow-safe Schrage step, as the C code does
        x = 171 * (x % 177) - 2 * (x // 177)
        y = 172 * (y % 176) - 35 * (y // 176)
        z = 170 * (z % 178) - 63 * (z // 178)
        if x < 0:
            x += 30269
        if y < 0:
            y += 30307
        if z < 0:
            z += 30323
        state = [x, y, z]

        def draw(n: int) -> int:
            cx, cy, cz = state
            value = int((cx / 30269.0 + cy / 30307.0 + cz / 30323.0) * n) % n
            state[0] = (171 * cx) % 30269
            state[1] = (172 * cy) % 30307
            state[2] = (170 * cz) % 30323
            return value

        return draw

    def _ensure_built(self) -> None:
        if self._forward is not None:
            return
        size, nrotors = self.size, self.nrotors
        draw = self._seeded_rng()
        identity = list(range(size + 1))
        forward, reverse, start = [], [], []
        for _ in range(nrotors):
            start.append(draw(size))
            e = identity[:]
            d = identity[:]
            step = 1 + 2 * draw(size // 2)      # odd advance step
            e[size] = d[size] = step            # stored past the alphabet
            i = size
            while i > 1:
                r = draw(i)
                i -= 1
                e[r], e[i] = e[i], e[r]
                d[e[i]] = i
            d[e[0]] = 0
            forward.append(tuple(e))
            reverse.append(tuple(d))
        self._forward = tuple(forward)
        self._reverse = tuple(reverse)
        self._start = tuple(start)

    def _advance(self, pos, rotors) -> None:
        """Step the rotors like an odometer, with carry."""
        size = self.size
        carry = 0
        for i in range(self.nrotors):
            total = ((pos[i] + carry) & 0xFF) + rotors[i][size]
            pos[i] = total % size
            carry = 1 if total >= size else 0

    def decrypt(self, data: bytes) -> bytes:
        self._ensure_built()
        reverse = self._reverse
        pos = list(self._start)
        out = bytearray(len(data))
        for k, c in enumerate(data):
            for i in range(self.nrotors - 1, -1, -1):
                c = pos[i] ^ reverse[i][c]
            out[k] = c & 0xFF
            self._advance(pos, reverse)
        return bytes(out)

    def encrypt(self, data: bytes) -> bytes:
        self._ensure_built()
        forward = self._forward
        pos = list(self._start)
        out = bytearray(len(data))
        for k, c in enumerate(data):
            for i in range(self.nrotors):
                c = forward[i][c ^ pos[i]]
            out[k] = c & 0xFF
            self._advance(pos, forward)
        return bytes(out)


# --- the four-step unwrap -----------------------------------------------------

REVEAL_XOR = 0x9A
REVEAL_XOR_LEN = 128
# First two bytes of a wrapped entry (rotor-encrypted zlib header); the bare,
# unencrypted bootstrap (redirect.nxs) starts with 0x63 (TYPE_CODE) instead.
WRAPPED_MARKERS = (b"\x1d\x04", b"\x15\x23")


def is_wrapped(data: bytes) -> bool:
    return data[:2] in WRAPPED_MARKERS


def unwrap(data: bytes, key: bytes | None = None) -> bytes:
    """One wrapped script entry -> its Python-2.7 marshal bytes."""
    rotor = Rotor(default_key() if key is None else key)
    plain = zlib.decompress(rotor.decrypt(data))
    head = bytes(b ^ REVEAL_XOR for b in plain[:REVEAL_XOR_LEN])
    unmasked = head + plain[REVEAL_XOR_LEN:]
    return unmasked[::-1]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: decrypt.py <wrapped-entry-in> <marshal-out>")
    blob = open(sys.argv[1], "rb").read()
    open(sys.argv[2], "wb").write(unwrap(blob))
