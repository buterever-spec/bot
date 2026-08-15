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
# CONFIG
# -----------------------------------------------------------------------------
MAX_SOURCE_BYTES = 750_000
ATTRIBUTION = "-- obfuscated by buterfuscate v14"
OBFUSCATION_LEVEL = "MAX"

# -----------------------------------------------------------------------------
# SHORT IDENTIFIERS (Luraph style)
# -----------------------------------------------------------------------------
_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

def _fresh_short(used: set[str]) -> str:
    for c in _CHARS:
        if c not in used:
            used.add(c); return c
    for c1 in _CHARS:
        for c2 in _CHARS:
            n = c1 + c2
            if n not in used:
                used.add(n); return n
    return "x"

def _fresh(used: set[str], *, confuse=False) -> str:
    return _fresh_short(used)

def _sep(a: str, b: str) -> bool:
    if not a or not b: return False
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
# ROBUST TOKENIZER
# -----------------------------------------------------------------------------
class TokenKind:
    WHITESPACE = "whitespace"; COMMENT = "comment"; LONG_COMMENT = "long_comment"
    STRING = "string"; LONG_STRING = "long_string"; NUMBER = "number"
    HEX_NUMBER = "hex_number"; IDENTIFIER = "identifier"; KEYWORD = "keyword"
    SYMBOL = "symbol"; EOF = "eof"

class Token:
    __slots__ = ("kind", "value", "line", "col")
    def __init__(self, kind, value, line=0, col=0):
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
    def _scan_string(self, quote):
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
# LITERAL BYTES
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
            d = e; c = i + 1
            while c < len(body) and len(d) < 3 and body[c].isdigit():
                d += body[c]; c += 1
            n = int(d)
            if n > 255: return None
            result.append(n); i = c; continue
        if e in {"\n", "\r"}:
            if e == "\r" and i + 1 < len(body) and body[i+1] == "\n": i += 1
            result.append(10); i += 1; continue
        return None
    return result

# -----------------------------------------------------------------------------
# SCOPE-AWARE RENAMING (locals only)
# -----------------------------------------------------------------------------
class Scope:
    def __init__(self, parent=None):
        self.parent = parent; self.names = set(); self.shadow = {}; self.children = []
    def declare(self, name): self.names.add(name)

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
                "bit32","string","table","math","os","debug","_G","_ENV","getfenv","_VERSION"}
    used = set(builtins)
    def assign(sc, used_set):
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
# INTEGER OBFUSCATION (hex + MBA)
# -----------------------------------------------------------------------------
def _mask_int(n: int) -> str:
    if n < 0: return "(-" + _mask_int(-n) + ")"
    if n in (0, 1): return hex(n)
    r = secrets.randbelow(5)
    if r == 0:
        k = secrets.randbelow(100) + 1
        return f"({hex(n+k)}-{hex(k)})"
    elif r == 1:
        k = secrets.randbelow(50) + 2
        return f"(({hex(n*k)})//{hex(k)})"
    elif r == 2:
        k = secrets.randbelow(0xFFFF) + 1
        return f"bit32.bxor({hex(n^k)},{hex(k)})"
    elif r == 3:
        k = secrets.randbelow(0xFF) + 1
        return f"bit32.band(bit32.bor({hex(n)},{hex(k)}),bit32.bxor({hex(n|k)},{hex(k^(n&k))}))"
    else:
        a = secrets.randbelow(0xFFF) + 1
        return f"bit32.bxor({hex(n^a)},{hex(a)})"

# -----------------------------------------------------------------------------
# STRING ENCRYPTION (variable split, rev, exact length)
# -----------------------------------------------------------------------------
def _pack_meta(split, seed, add, step, blk, rot, rev):
    return (split & 0xF) | ((seed & 0xFF) << 4) | ((add & 0xFF) << 12) | ((step & 0x1F) << 20) | ((blk & 0xFF) << 25) | ((rot & 0x7) << 33) | ((rev & 0x1) << 36)

def _encrypt_string(plain: str, seed: int) -> dict:
    # plain must be a string literal (with quotes or brackets)
    bytes_ = _literal_bytes(plain)
    if bytes_ is None:
        raise ValueError(f"Invalid string literal: {plain}")
    n = len(bytes_)
    if n == 0: return {"chunks": [[]], "meta": 0, "chk": 2166136261, "len": 0}
    rng = random.Random(seed)
    split = rng.randint(2, 5)
    seed_val = rng.randint(1, 251); add = rng.randint(0, 250); step = rng.randint(1, 31)
    blk = rng.randint(1, 251); rot = rng.randint(1, 7); rev = rng.randint(0, 1)
    s1 = [b ^ ((seed_val + i * step + add) % 256) for i, b in enumerate(bytes_)]
    s2 = [((b << rot) | (b >> (8 - rot))) & 0xFF for b in s1]
    s3 = [b ^ blk for b in s2]
    if rev: s3 = s3[::-1]
    chunks = [[] for _ in range(split)]
    for i, b in enumerate(s3): chunks[i % split].append(b)
    chk = 2166136261
    for b in bytes_: chk = ((chk ^ b) * 16777619) & 0xFFFFFFFF
    meta = _pack_meta(split, seed_val, add, step, blk, rot, rev)
    return {"chunks": chunks, "meta": meta, "chk": chk, "len": n}

# -----------------------------------------------------------------------------
# GLOBAL REPLACEMENT (hide global function names)
# -----------------------------------------------------------------------------
def _replace_globals(tokens: List[Token], global_map: Dict[str, int], lookup_name: str) -> List[Token]:
    out = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind == TokenKind.IDENTIFIER and t.value in global_map:
            # check if it's a function call (next non-whitespace is '(')
            j = i + 1
            while j < len(tokens) and tokens[j].kind == TokenKind.WHITESPACE:
                j += 1
            if j < len(tokens) and tokens[j].value == '(':
                # ensure not preceded by '.' or ':' (method call)
                if i > 0 and tokens[i-1].value not in ('.', ':'):
                    # replace with lookup_name[decode(idx)]
                    idx = global_map[t.value]
                    out.append(Token(TokenKind.IDENTIFIER, lookup_name, t.line, t.col))
                    out.append(Token(TokenKind.SYMBOL, "[", t.line, t.col))
                    out.append(Token(TokenKind.IDENTIFIER, f"__G{idx}__", t.line, t.col))
                    out.append(Token(TokenKind.SYMBOL, "]", t.line, t.col))
                    i += 1
                    continue
        out.append(t)
        i += 1
    return out

# -----------------------------------------------------------------------------
# RUNTIME GENERATOR
# -----------------------------------------------------------------------------
def generate_runtime(records: List[dict], used: set, global_map: Dict[str, int]) -> Tuple[str, str]:
    """Returns (runtime_code, decode_function_name, global_lookup_name)"""
    N = lambda: _fresh(used, confuse=False)
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

    # Global lookup table – will be used as G[decoded_name]
    lookup_name = N()
    env_decl = f"local {lookup_name}=getfenv and getfenv(0) or _G or _ENV\n"

    body = f"""local {bxor},{bor},{band},{lshift},{rshift}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {char},{concat},{floor}=string.char,table.concat,math.floor
local {trap}=function()error("",0)end
{env_decl}
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
local pos=i
if rev==1 then pos=n-i+1 end
out[pos]={char}(v)
end
if chk~=expected then {trap}() end
local result={concat}(out)
{cache}[idx]=result
return result
end"""
    return body, decode_fn, lookup_name

# -----------------------------------------------------------------------------
# MINIFICATION (one line)
# -----------------------------------------------------------------------------
def _minify(code: str) -> str:
    tokens = Scanner(code).scan()
    filtered = [t for t in tokens if t.kind not in (TokenKind.COMMENT, TokenKind.LONG_COMMENT, TokenKind.WHITESPACE)]
    return _compact([t.value for t in filtered])

# -----------------------------------------------------------------------------
# VALIDATION (luac -p if available)
# -----------------------------------------------------------------------------
def _validate(code: str) -> bool:
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False) as f:
            f.write(code); f.flush()
            result = subprocess.run(["luac", "-p", f.name], capture_output=True, text=True)
            return result.returncode == 0
    except: return True

# -----------------------------------------------------------------------------
# MAIN OBFUSCATOR
# -----------------------------------------------------------------------------
def obfuscate_luau(source: str) -> str:
    # 1. Rename locals
    code = _rename_locals(source)

    # 2. Tokenize
    tokens = Scanner(code).scan()

    # 3. Build global map (identifiers not in local scopes)
    root = _build_scopes(tokens)
    def is_local(name, sc):
        if name in sc.names: return True
        for child in sc.children:
            if is_local(name, child): return True
        return False

    global_map = {}
    for t in tokens:
        if t.kind == TokenKind.IDENTIFIER:
            if not is_local(t.value, root):
                if t.value not in global_map:
                    global_map[t.value] = 0
    # assign indices
    names = list(global_map.keys())
    for i, name in enumerate(names):
        global_map[name] = i + 1  # 1-indexed

    # 4. Replace globals with lookup table (placeholders)
    lookup_name = _fresh(set(), confuse=False)
    tokens = _replace_globals(tokens, global_map, lookup_name)

    # 5. Process tokens: encrypt strings, mask numbers, collect records
    records = []
    out_tokens = []
    # We also need to encrypt the global names for the lookup table
    # We'll add the global names as strings to the records
    global_strings = {}
    for name, idx in global_map.items():
        # IMPORTANT: wrap the name in quotes so _literal_bytes can parse it
        enc = _encrypt_string('"' + name + '"', secrets.randbelow(0xFFFFFFFF))
        global_strings[idx] = len(records)
        records.append(enc)

    for t in tokens:
        if t.kind in (TokenKind.STRING, TokenKind.LONG_STRING):
            enc = _encrypt_string(t.value, secrets.randbelow(0xFFFFFFFF))
            records.append(enc)
            out_tokens.append(f"__R{len(records)-1}__")
        elif t.kind == TokenKind.IDENTIFIER and t.value.startswith("__G") and t.value.endswith("__"):
            # This is a global placeholder like __G1__
            idx = int(t.value[3:-2])
            # Replace with lookup_name[decode(record_index)]
            rec_idx = global_strings[idx]
            out_tokens.append(f"{lookup_name}[{rec_idx}]")
        elif t.kind in (TokenKind.NUMBER, TokenKind.HEX_NUMBER):
            try:
                val = int(t.value, 0)
                if abs(val) < 1000000:
                    out_tokens.append(_mask_int(val))
                else: out_tokens.append(t.value)
            except: out_tokens.append(t.value)
        else:
            out_tokens.append(t.value)

    # 6. Shuffle records
    order = list(range(len(records)))
    secrets.SystemRandom().shuffle(order)
    ordered = [records[i] for i in order]

    # 7. Generate runtime
    runtime_code, decode_fn, global_lookup = generate_runtime(ordered, set(), global_map)

    # 8. Replace placeholders in body tokens
    body_tokens = []
    for tok in out_tokens:
        if tok.startswith("__R") and tok.endswith("__"):
            idx = int(tok[3:-2])
            body_tokens.append(f"{decode_fn}({order.index(idx)})")
        else:
            body_tokens.append(tok)
    body = " ".join(body_tokens)

    # 9. Control-flow flattening (lightweight)
    state = _fresh(set())
    body_lines = body.splitlines()
    flattened = [f"local {state}=0", "while true do", f"if {state}==0 then"]
    flattened.extend("    " + l for l in body_lines)
    flattened.append(f"    {state}=1")
    flattened.append(f"elseif {state}==1 then")
    flattened.append("    break")
    flattened.append("end")
    flattened.append("end")
    body = "\n".join(flattened)

    # 10. Junk code (safe)
    if OBFUSCATION_LEVEL in ("MAX", "HIGH"):
        junk = []
        if secrets.randbelow(100) < 30:
            v = _fresh(set()); junk.append(f"local {v}={_mask_int(secrets.randbelow(1000))}")
        if secrets.randbelow(100) < 20:
            junk.append("if false then end")
        if junk:
            body = "\n".join(junk) + "\n" + body

    # 11. Assemble IIFE: (function() ... end)()
    func_body = runtime_code + "\n" + body
    final = f"(function(){func_body}end)()"

    # 12. Minify to one line
    final = _minify(final)

    # 13. Validate
    if not _validate(final):
        raise RuntimeError("Validation failed – generated code invalid")

    return final

# -----------------------------------------------------------------------------
# DISCORD COG
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
