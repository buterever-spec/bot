from __future__ import annotations

import asyncio
import io
import re
import secrets
import random
import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Set, Dict, Tuple, List, Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
MAX_SOURCE_BYTES = 750_000
ATTRIBUTION = "-- obfuscated by buterfuscate v12"
OBFUSCATION_LEVEL = "MAX"
DEBUG_MODE = False

# -----------------------------------------------------------------------------
# CORE HELPERS – generate very short identifiers (Luraph style)
# -----------------------------------------------------------------------------
_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

def _fresh_short(used: set[str]) -> str:
    for c in _CHARS:
        if c not in used:
            used.add(c)
            return c
    for c1 in _CHARS:
        for c2 in _CHARS:
            name = c1 + c2
            if name not in used:
                used.add(name)
                return name
    return "x"

def _fresh(used: set[str], *, confuse: bool = False) -> str:
    return _fresh_short(used)

def _sep(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if (a[-1].isalnum() or a[-1] == "_") and (b[0].isalnum() or b[0] == "_"):
        return True
    return a.endswith("-") and b.startswith("-")

def _compact(tokens: list[str]) -> str:
    out = []
    for t in tokens:
        if out and _sep(out[-1], t):
            out.append(" ")
        out.append(t)
    return "".join(out)

# -----------------------------------------------------------------------------
# 1. ROBUST TOKENIZER (handles ALL string/comment forms)
# -----------------------------------------------------------------------------
class TokenKind:
    WHITESPACE = "whitespace"; COMMENT = "comment"; LONG_COMMENT = "long_comment"
    STRING = "string"; LONG_STRING = "long_string"; NUMBER = "number"
    HEX_NUMBER = "hex_number"; IDENTIFIER = "identifier"; KEYWORD = "keyword"
    SYMBOL = "symbol"; EOF = "eof"

class Token:
    __slots__ = ("kind", "value", "line", "col")
    def __init__(self, kind: str, value: str, line: int = 0, col: int = 0):
        self.kind = kind; self.value = value; self.line = line; self.col = col

class Scanner:
    def __init__(self, source: str):
        self.source = source; self.length = len(source); self.pos = 0
        self.line = 1; self.col = 1; self.tokens = []
    def scan(self) -> List[Token]:
        while self.pos < self.length:
            ch = self.source[self.pos]
            if ch in " \t\r\n": self._scan_whitespace()
            elif ch == '-' and self.pos + 1 < self.length and self.source[self.pos+1] == '-': self._scan_comment()
            elif ch in "\"'": self._scan_string(ch)
            elif ch == '[' and self.pos + 1 < self.length and self.source[self.pos+1] in "[":
                self._scan_long_string()
            elif ch.isdigit() or (ch == '.' and self.pos+1 < self.length and self.source[self.pos+1].isdigit()):
                self._scan_number()
            elif ch.isalpha() or ch == '_': self._scan_identifier_or_keyword()
            else: self._scan_symbol()
        self.tokens.append(Token(TokenKind.EOF, "", self.line, self.col))
        return self.tokens
    def _scan_whitespace(self):
        start = self.pos
        while self.pos < self.length and self.source[self.pos] in " \t\r\n":
            if self.source[self.pos] == '\n': self.line += 1; self.col = 1
            else: self.col += 1
            self.pos += 1
        self.tokens.append(Token(TokenKind.WHITESPACE, self.source[start:self.pos], self.line, self.col))
    def _scan_comment(self):
        start = self.pos
        if self.pos + 2 < self.length and self.source[self.pos+2] == '[':
            self.pos += 2; self.col += 2; eq = 0
            while self.pos < self.length and self.source[self.pos] == '=': eq += 1; self.pos += 1; self.col += 1
            if self.pos < self.length and self.source[self.pos] == '[':
                self.pos += 1; self.col += 1; close = ']' + '=' * eq + ']'
                end = self.source.find(close, self.pos)
                if end != -1:
                    end += len(close)
                    self.tokens.append(Token(TokenKind.LONG_COMMENT, self.source[start:end], self.line, self.col))
                    self.pos = end; self.col = 1; return
        self.pos += 2; self.col += 2
        while self.pos < self.length and self.source[self.pos] != '\n': self.pos += 1; self.col += 1
        self.tokens.append(Token(TokenKind.COMMENT, self.source[start:self.pos], self.line, self.col))
    def _scan_string(self, quote: str):
        start = self.pos; self.pos += 1; self.col += 1
        while self.pos < self.length:
            ch = self.source[self.pos]
            if ch == '\\': self.pos += 2; self.col += 2
            elif ch == quote: self.pos += 1; self.col += 1; self.tokens.append(Token(TokenKind.STRING, self.source[start:self.pos], self.line, self.col)); return
            elif ch == '\n': self.line += 1; self.col = 1; self.pos += 1
            else: self.pos += 1; self.col += 1
        self.tokens.append(Token(TokenKind.STRING, self.source[start:self.pos], self.line, self.col))
    def _scan_long_string(self):
        start = self.pos; self.pos += 2; self.col += 2; eq = 0
        while self.pos < self.length and self.source[self.pos] == '=': eq += 1; self.pos += 1; self.col += 1
        if self.pos < self.length and self.source[self.pos] == '[':
            self.pos += 1; self.col += 1; close = ']' + '=' * eq + ']'
            end = self.source.find(close, self.pos)
            if end != -1:
                end += len(close)
                self.tokens.append(Token(TokenKind.LONG_STRING, self.source[start:end], self.line, self.col))
                self.pos = end; self.col = 1; return
        self.tokens.append(Token(TokenKind.STRING, self.source[start:self.pos], self.line, self.col))
    def _scan_number(self):
        start = self.pos
        if self.source[self.pos] == '0' and self.pos+1 < self.length and self.source[self.pos+1].lower() == 'x':
            self.pos += 2
            while self.pos < self.length and self.source[self.pos].isalnum(): self.pos += 1
            self.tokens.append(Token(TokenKind.HEX_NUMBER, self.source[start:self.pos], self.line, self.col)); return
        while self.pos < self.length and (self.source[self.pos].isdigit() or self.source[self.pos] == '.'): self.pos += 1
        if self.pos < self.length and self.source[self.pos] in 'eE':
            self.pos += 1
            if self.pos < self.length and self.source[self.pos] in '+-': self.pos += 1
            while self.pos < self.length and self.source[self.pos].isdigit(): self.pos += 1
        self.tokens.append(Token(TokenKind.NUMBER, self.source[start:self.pos], self.line, self.col))
    def _scan_identifier_or_keyword(self):
        start = self.pos
        while self.pos < self.length and (self.source[self.pos].isalnum() or self.source[self.pos] == '_'): self.pos += 1
        ident = self.source[start:self.pos]
        keywords = {"and","break","do","else","elseif","end","false","for","function","goto",
                    "if","in","local","nil","not","or","repeat","return","then","true","until","while"}
        kind = TokenKind.KEYWORD if ident in keywords else TokenKind.IDENTIFIER
        self.tokens.append(Token(kind, ident, self.line, self.col))
    def _scan_symbol(self):
        start = self.pos; self.pos += 1; self.col += 1
        self.tokens.append(Token(TokenKind.SYMBOL, self.source[start:self.pos], self.line, self.col))

# -----------------------------------------------------------------------------
# 2. STRING LITERAL PARSER (exact byte extraction)
# -----------------------------------------------------------------------------
def _literal_bytes(val: str) -> list[int] | None:
    if val.startswith("["):
        m = re.match(r"^\[(=*)\[(.*)\]\1\]$", val, re.DOTALL)
        return list(m.group(2).encode()) if m else None
    if len(val) < 2 or val[0] not in {'"', "'"} or val[-1] != val[0]: return None
    body = val[1:-1]; result = []; i = 0
    esc = {"a":7,"b":8,"f":12,"n":10,"r":13,"t":9,"v":11,"\\":92,"'":39,'"':34}
    while i < len(body):
        ch = body[i]
        if ch != "\\": result.extend(ch.encode()); i += 1; continue
        i += 1
        if i >= len(body): return None
        e = body[i]
        if e in esc: result.append(esc[e]); i += 1; continue
        if e == "x" and i + 2 < len(body):
            d = body[i+1:i+3]
            if re.fullmatch(r"[0-9a-fA-F]{2}", d):
                result.append(int(d, 16)); i += 3; continue
        if e.isdigit():
            d = e; c = i+1
            while c < len(body) and len(d) < 3 and body[c].isdigit():
                d += body[c]; c += 1
            n = int(d)
            if n > 255: return None
            result.append(n); i = c; continue
        if e in {"\n","\r"}:
            if e == "\r" and i + 1 < len(body) and body[i+1] == "\n": i += 1
            result.append(10); i += 1; continue
        return None
    return result

# -----------------------------------------------------------------------------
# 3. SCOPE-AWARE RENAMING (locals only, short names)
# -----------------------------------------------------------------------------
class Scope:
    def __init__(self, parent: Optional[Scope] = None):
        self.parent = parent; self.names = set(); self.shadow = {}
        self.children = []
    def declare(self, name: str): self.names.add(name)

def _build_scopes(tokens: List[Token]) -> Scope:
    root = Scope(); stack = [root]; cur = root; i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind == TokenKind.KEYWORD and t.value == "local":
            i += 1
            while i < len(tokens) and tokens[i].kind == TokenKind.WHITESPACE: i += 1
            while i < len(tokens):
                if tokens[i].kind == TokenKind.IDENTIFIER:
                    cur.declare(tokens[i].value); i += 1
                    while i < len(tokens) and tokens[i].kind == TokenKind.WHITESPACE: i += 1
                    if i < len(tokens) and tokens[i].value == ",":
                        i += 1; continue
                    else: break
                else: break
            continue
        elif t.kind == TokenKind.KEYWORD and t.value in ("function","do","then","repeat"):
            new = Scope(cur); cur.children.append(new); stack.append(new); cur = new; i += 1; continue
        elif t.kind == TokenKind.KEYWORD and t.value in ("end","until"):
            if len(stack) > 1: stack.pop(); cur = stack[-1]
            i += 1; continue
        i += 1
    return root

def _rename_locals(code: str) -> str:
    tokens = Scanner(code).scan()
    root = _build_scopes(tokens)
    builtins = {"print","warn","error","require","pairs","ipairs","next","typeof","Instance",
                "bit32","string","table","math","os","debug","_G","_ENV"}
    used = set(builtins)
    def assign(sc: Scope, used_set: Set[str]):
        for name in list(sc.names):
            if name in builtins or name.startswith("_"): continue
            new = _fresh(used_set, confuse=False)
            sc.shadow[name] = new
            used_set.add(new)
        for ch in sc.children: assign(ch, used_set.copy())
    assign(root, set(builtins))
    stack = [root]; cur = root; out = []
    for t in tokens:
        if t.kind == TokenKind.KEYWORD and t.value in ("function","do","then","repeat"):
            if cur.children: cur = cur.children[-1]; stack.append(cur)
            out.append(t.value)
        elif t.kind == TokenKind.KEYWORD and t.value in ("end","until"):
            if len(stack) > 1: stack.pop(); cur = stack[-1]
            out.append(t.value)
        elif t.kind == TokenKind.IDENTIFIER:
            resolved = False
            for sc in reversed(stack):
                if t.value in sc.shadow:
                    out.append(sc.shadow[t.value]); resolved = True; break
                elif t.value in sc.names:
                    out.append(t.value); resolved = True; break
            if not resolved: out.append(t.value)
        else: out.append(t.value)
    return " ".join(out)  # will be compacted later

# -----------------------------------------------------------------------------
# 4. INTEGER OBFUSCATION (hex + MBA)
# -----------------------------------------------------------------------------
def _mask_int(n: int) -> str:
    if n < 0: return "(-" + _mask_int(-n) + ")"
    if n in (0,1): return hex(n)
    r = secrets.randbelow(5)
    if r == 0:
        k = secrets.randbelow(100)+1
        return f"({hex(n+k)}-{hex(k)})"
    elif r == 1:
        k = secrets.randbelow(50)+2
        return f"(({hex(n*k)})//{hex(k)})"
    elif r == 2:
        k = secrets.randbelow(0xFFFF)+1
        return f"bit32.bxor({hex(n^k)},{hex(k)})"
    elif r == 3:
        k = secrets.randbelow(0xFF)+1
        return f"bit32.band(bit32.bor({hex(n)},{hex(k)}),bit32.bxor({hex(n|k)},{hex(k^(n&k))}))"
    else:
        a = secrets.randbelow(0xFFF)+1
        return f"bit32.bxor({hex(n^a)},{hex(a)})"

# -----------------------------------------------------------------------------
# 5. STRING ENCRYPTION (per‑string random transforms, exact length)
# -----------------------------------------------------------------------------
def _pack_meta(split: int, seed: int, add: int, step: int, blk: int, rot: int, rev: int) -> int:
    return (split & 0xF) | ((seed & 0xFF) << 4) | ((add & 0xFF) << 12) | ((step & 0x1F) << 20) | ((blk & 0xFF) << 25) | ((rot & 0x7) << 33) | ((rev & 0x1) << 36)

def _encrypt_string(plain: str, seed: int) -> dict:
    bytes_ = _literal_bytes(plain)
    if bytes_ is None: raise ValueError("Invalid string literal")
    n = len(bytes_)
    if n == 0: return {"chunks": [[]], "meta": 0, "chk": 2166136261, "len": 0}
    rng = random.Random(seed)
    split = rng.randint(2,5)
    seed_val = rng.randint(1,251); add = rng.randint(0,250); step = rng.randint(1,31)
    blk = rng.randint(1,251); rot = rng.randint(1,7); rev = rng.randint(0,1)
    s1 = [b ^ ((seed_val + i*step + add) % 256) for i,b in enumerate(bytes_)]
    s2 = [((b << rot) | (b >> (8-rot))) & 0xFF for b in s1]
    s3 = [b ^ blk for b in s2]
    if rev: s3 = s3[::-1]
    chunks = [[] for _ in range(split)]
    for i,b in enumerate(s3): chunks[i % split].append(b)
    chk = 2166136261
    for b in bytes_: chk = ((chk ^ b) * 16777619) & 0xFFFFFFFF
    meta = _pack_meta(split, seed_val, add, step, blk, rot, rev)
    return {"chunks": chunks, "meta": meta, "chk": chk, "len": n}

# -----------------------------------------------------------------------------
# 6. GLOBAL FUNCTION LOOKUP TABLE (hide global names)
# -----------------------------------------------------------------------------
def _build_global_map(tokens: List[Token]) -> Dict[str, int]:
    """Find identifiers used as function calls (not preceded by '.' or ':')."""
    globals_used = {}
    idx = 0
    for i, t in enumerate(tokens):
        if t.kind == TokenKind.IDENTIFIER:
            # check if next non‑whitespace is '('
            j = i + 1
            while j < len(tokens) and tokens[j].kind == TokenKind.WHITESPACE:
                j += 1
            if j < len(tokens) and tokens[j].value == '(':
                # ensure not a method call (preceded by '.' or ':')
                if i > 0 and tokens[i-1].value not in ('.', ':'):
                    # also check if it's a local (we'll handle later)
                    globals_used[t.value] = 0  # placeholder
    # assign random indices
    names = list(globals_used.keys())
    for name in names:
        globals_used[name] = secrets.randbelow(0xFFFF) + 1
    return globals_used

def _replace_globals(tokens: List[Token], global_map: Dict[str, int], lookup_name: str) -> List[Token]:
    out = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind == TokenKind.IDENTIFIER and t.value in global_map:
            j = i + 1
            while j < len(tokens) and tokens[j].kind == TokenKind.WHITESPACE:
                j += 1
            if j < len(tokens) and tokens[j].value == '(':
                if i > 0 and tokens[i-1].value not in ('.', ':'):
                    # replace with lookup_name[index]
                    out.append(Token(TokenKind.IDENTIFIER, lookup_name, t.line, t.col))
                    out.append(Token(TokenKind.SYMBOL, "[", t.line, t.col))
                    out.append(Token(TokenKind.NUMBER, str(global_map[t.value]), t.line, t.col))
                    out.append(Token(TokenKind.SYMBOL, "]", t.line, t.col))
                    i += 1
                    continue
        out.append(t)
        i += 1
    return out

# -----------------------------------------------------------------------------
# 7. RUNTIME GENERATOR (Luraph‑style return table)
# -----------------------------------------------------------------------------
def generate_runtime(records: List[dict], used: set, global_map: Dict[str, int]) -> Tuple[str, str]:
    """Returns (runtime_code, decode_function_name)"""
    N = lambda: _fresh(used, confuse=False)
    # Short names for all locals
    bxor, bor, band, lshift, rshift = N(), N(), N(), N(), N()
    char, concat, floor = N(), N(), N()
    trap, meta_tbl, chunk_tbl, len_tbl, chk_tbl, cache, decode_fn = N(), N(), N(), N(), N(), N(), N()

    meta_list = [str(hex(r["meta"])) for r in records]
    chunk_list = []
    for r in records:
        chunk_strs = ["{" + ",".join(hex(b) for b in ch) + "}" for ch in r["chunks"]]
        chunk_list.append("{" + ",".join(chunk_strs) + "}")
    len_list = [str(hex(r["len"])) for r in records]
    chk_list = [str(hex(r["chk"])) for r in records]

    # Build global lookup table
    lookup_name = N()
    if global_map:
        lookup_assigns = []
        for name, idx in global_map.items():
            lookup_assigns.append(f"{lookup_name}[{hex(idx)}]={name}")
        lookup_decl = f"local {lookup_name}={{}}\n" + "\n".join(lookup_assigns)
    else:
        lookup_decl = ""

    # Decoder body
    body = f"""local {bxor},{bor},{band},{lshift},{rshift}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {char},{concat},{floor}=string.char,table.concat,math.floor
local {trap}=function()error("",0)end
{lookup_decl}
local {meta_tbl}={{{",".join(meta_list)}}}
local {chunk_tbl}={{{",".join(chunk_list)}}}
local {len_tbl}={{{",".join(len_list)}}}
local {chk_tbl}={{{",".join(chk_list)}}}
local {cache}={{}}
local function {decode_fn}(idx)
if {cache}[idx] then return {cache}[idx] end
local m={meta_tbl}[idx+1]
local n={len_tbl}[idx+1]
local expected={chk_tbl}[idx+1]
if not m then {trap}() end
local split={band}(m,0xF)
local seed={band}({rshift}(m,0x4),0xFF)
local add={band}({rshift}(m,0xC),0xFF)
local step={band}({rshift}(m,0x14),0x1F)
local blk={band}({rshift}(m,0x19),0xFF)
local rot={band}({rshift}(m,0x21),0x7)
local rev={band}({rshift}(m,0x24),0x1)
local chunks={chunk_tbl}[idx+1]
local out={{}}; local chk=0x81F
for i=1,n do
local ci=(i-1)%split+1
local chunk=chunks[ci]
if not chunk then {trap}() end
local bi=(i-1)//split+1
local b=chunk[bi]
if not b then {trap}() end
local v=b
v={bxor}(v,blk)
v={bor}({rshift}(v,rot),{lshift}(v,0x8-rot))
v={band}(v,0xFF)
v={bxor}(v,(seed+(i-1)*step+add)%0x100)
if v<0 then v=v+0x100 end
chk={band}(({bxor}(chk,v)*0x1000193),0xFFFFFFFF)
local pos = i
if rev==1 then pos = n - i + 1 end
out[pos]={char}(v)
end
if chk~=expected then {trap}() end
local result={concat}(out)
{cache}[idx]=result
return result
end"""
    return body, decode_fn

# -----------------------------------------------------------------------------
# 8. MINIFICATION (one line)
# -----------------------------------------------------------------------------
def _minify(code: str) -> str:
    tokens = Scanner(code).scan()
    filtered = [t for t in tokens if t.kind not in (TokenKind.COMMENT, TokenKind.LONG_COMMENT, TokenKind.WHITESPACE)]
    return _compact([t.value for t in filtered])

# -----------------------------------------------------------------------------
# 9. VALIDATION (optional luac -p)
# -----------------------------------------------------------------------------
def _validate(code: str) -> bool:
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False) as f:
            f.write(code); f.flush()
            result = subprocess.run(["luac", "-p", f.name], capture_output=True, text=True)
            return result.returncode == 0
    except: return True

# -----------------------------------------------------------------------------
# 10. MAIN OBFUSCATOR
# -----------------------------------------------------------------------------
def obfuscate_luau(source: str) -> str:
    # Step 1: rename locals
    code = _rename_locals(source)

    # Step 2: tokenize
    tokens = Scanner(code).scan()

    # Step 3: build global map (for function calls)
    global_map = _build_global_map(tokens)
    lookup_name = _fresh(set(), confuse=False)  # will be used later

    # Step 4: process tokens – encrypt strings, mask numbers, collect records
    used = set()
    records = []
    out_tokens = []
    for t in tokens:
        if t.kind in (TokenKind.STRING, TokenKind.LONG_STRING):
            enc = _encrypt_string(t.value, secrets.randbelow(0xFFFFFFFF))
            records.append(enc)
            out_tokens.append(f"__R{len(records)-1}__")
        elif t.kind in (TokenKind.NUMBER, TokenKind.HEX_NUMBER):
            try:
                val = int(t.value, 0)
                if abs(val) < 1000000:
                    out_tokens.append(_mask_int(val))
                else: out_tokens.append(t.value)
            except: out_tokens.append(t.value)
        else:
            out_tokens.append(t.value)

    # Step 5: replace globals with lookup table
    # We'll need to tokenize out_tokens again? Instead we can use the original tokens
    # and replace after we have the lookup name.
    # We'll reconstruct tokens from out_tokens? Simpler: we already have the token list,
    # we can modify it.
    # Let's do a pass on the original token list and replace globals.
    # But we need the lookup_name; we already generated it.
    # We'll replace in the original tokens before we convert to out_tokens?
    # We'll re-tokenize the renamed code? Actually we have the renamed code as a string.
    # Better: after renaming, we tokenize, build global_map, then do replacement on tokens
    # before processing strings/numbers.
    # We'll restructure:
    #   - rename -> code
    #   - tokenize -> tokens
    #   - build global_map
    #   - replace globals in tokens (with lookup_name)
    #   - then process tokens to encrypt strings, etc.
    # But the global replacement also replaces function names like 'print' with lookup table.
    # We need to ensure the lookup table is defined in the runtime.

    # Let's rebuild the tokens after global replacement
    tokens_replaced = _replace_globals(tokens, global_map, lookup_name)

    # Now process tokens_replaced
    records = []
    out_tokens = []
    for t in tokens_replaced:
        if t.kind in (TokenKind.STRING, TokenKind.LONG_STRING):
            enc = _encrypt_string(t.value, secrets.randbelow(0xFFFFFFFF))
            records.append(enc)
            out_tokens.append(f"__R{len(records)-1}__")
        elif t.kind in (TokenKind.NUMBER, TokenKind.HEX_NUMBER):
            try:
                val = int(t.value, 0)
                if abs(val) < 1000000:
                    out_tokens.append(_mask_int(val))
                else: out_tokens.append(t.value)
            except: out_tokens.append(t.value)
        else:
            out_tokens.append(t.value)

    # Shuffle records
    order = list(range(len(records)))
    secrets.SystemRandom().shuffle(order)
    ordered = [records[i] for i in order]

    # Generate runtime (includes lookup table)
    runtime_code, decode_fn = generate_runtime(ordered, used, global_map)

    # Replace placeholders in body
    body_tokens = []
    for tok in out_tokens:
        if tok.startswith("__R") and tok.endswith("__"):
            idx = int(tok[3:-2])
            body_tokens.append(f"{decode_fn}({order.index(idx)})")
        else:
            body_tokens.append(tok)
    body = " ".join(body_tokens)

    # Add control-flow flattening (lightweight)
    state = _fresh(used)
    body_lines = body.splitlines()
    flattened = [f"local {state}=0", "while true do", f"if {state}==0 then"]
    flattened.extend("    " + l for l in body_lines)
    flattened.append(f"    {state}=1")
    flattened.append(f"elseif {state}==1 then")
    flattened.append("    break")
    flattened.append("end")
    flattened.append("end")
    body = "\n".join(flattened)

    # Inject junk (optional)
    if OBFUSCATION_LEVEL in ("MAX","HIGH"):
        junk = []
        if secrets.randbelow(100) < 30:
            v = _fresh(used); junk.append(f"local {v}={_mask_int(secrets.randbelow(1000))}")
        if secrets.randbelow(100) < 20:
            junk.append("if false then end")
        if junk:
            body = "\n".join(junk) + "\n" + body

    # Assemble final: return({L=function() ... end})()
    # runtime_code contains the decoder and lookup table; we put it inside the function.
    # The body is placed after the runtime.
    func_body = runtime_code + "\n" + body
    final_code = f"return({{L=function(){func_body}end}})()"

    # Minify to one line
    final_code = _minify(final_code)

    # Validate
    if not _validate(final_code):
        raise RuntimeError("Validation failed – generated code invalid")

    return final_code

# -----------------------------------------------------------------------------
# 11. DISCORD COG
# -----------------------------------------------------------------------------
def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.txt"

class Obfuscation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="obf", description="Obfuscate a Luau .TXT file")
    @app_commands.describe(file="Attach the .TXT Luau source file to obfuscate")
    async def obf(self, interaction: discord.Interaction, file: discord.Attachment | None = None):
        if file is None:
            await interaction.response.send_message("Attach a `.TXT` file to continue.", ephemeral=True)
            return
        if not file.filename.lower().endswith(".txt"):
            await interaction.response.send_message("Only `.TXT` files are supported.", ephemeral=True)
            return
        if file.size and file.size > MAX_SOURCE_BYTES:
            await interaction.response.send_message(f"Too large. Limit is {MAX_SOURCE_BYTES // 1000} KB.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            src = await file.read()
            if len(src) > MAX_SOURCE_BYTES:
                raise ValueError("Too large.")
            source = src.decode("utf-8-sig")
            if not source.strip():
                raise ValueError("Empty file.")
            obf = await asyncio.to_thread(obfuscate_luau, source)
            out = discord.File(io.BytesIO(obf.encode()), filename=_output_name(file.filename))
            await interaction.followup.send(content="- obfuscated by buterfuscate", file=out, allowed_mentions=discord.AllowedMentions.none())
        except UnicodeDecodeError:
            await interaction.followup.send("Could not read as UTF-8.", ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
        except Exception as e:
            print(f"Obfuscation error: {e}")
            await interaction.followup.send("Obfuscation failed. Check the file and try again.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
