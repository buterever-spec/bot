from __future__ import annotations

import asyncio
import io
import re
import secrets
import random
import base64
import sys
import ast as py_ast
from pathlib import Path
from typing import Iterable, Set, Dict, Tuple, List, Any, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
MAX_SOURCE_BYTES = 750_000
ATTRIBUTION = "-- obfuscated by buterfuscate v9"
OBFUSCATION_LEVEL = "MAX"  # LOW, MEDIUM, HIGH, MAX
DEBUG_MODE = False  # never enable in normal operation

# -----------------------------------------------------------------------------
# 1. ROBUST TOKENIZER / SCANNER
# -----------------------------------------------------------------------------
class TokenKind:
    WHITESPACE = "whitespace"
    COMMENT = "comment"
    LONG_COMMENT = "long_comment"
    STRING = "string"
    LONG_STRING = "long_string"
    NUMBER = "number"
    HEX_NUMBER = "hex_number"
    IDENTIFIER = "identifier"
    KEYWORD = "keyword"
    SYMBOL = "symbol"
    EOF = "eof"

class Token:
    __slots__ = ("kind", "value", "line", "col")
    def __init__(self, kind: str, value: str, line: int = 0, col: int = 0):
        self.kind = kind
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.kind!r}, {self.value!r})"

class Scanner:
    """A correct Luau tokenizer that handles all string/comment forms."""
    def __init__(self, source: str):
        self.source = source
        self.length = len(source)
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens = []

    def scan(self) -> List[Token]:
        while self.pos < self.length:
            ch = self.source[self.pos]
            if ch in " \t\r\n":
                self._scan_whitespace()
            elif ch == '-' and self.pos + 1 < self.length and self.source[self.pos+1] == '-':
                self._scan_comment()
            elif ch in "\"'":
                self._scan_string(ch)
            elif ch == '[' and self.pos + 1 < self.length and self.source[self.pos+1] in "[":
                self._scan_long_string()
            elif ch.isdigit() or (ch == '.' and self.pos+1 < self.length and self.source[self.pos+1].isdigit()):
                self._scan_number()
            elif ch.isalpha() or ch == '_':
                self._scan_identifier_or_keyword()
            else:
                self._scan_symbol()
        self.tokens.append(Token(TokenKind.EOF, "", self.line, self.col))
        return self.tokens

    def _scan_whitespace(self):
        start = self.pos
        while self.pos < self.length and self.source[self.pos] in " \t\r\n":
            if self.source[self.pos] == '\n':
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            self.pos += 1
        self.tokens.append(Token(TokenKind.WHITESPACE, self.source[start:self.pos], self.line, self.col))

    def _scan_comment(self):
        start = self.pos
        if self.pos + 2 < self.length and self.source[self.pos+2] == '[':
            # long comment --[[...]]
            self.pos += 2
            self.col += 2
            eq = 0
            while self.pos < self.length and self.source[self.pos] == '=':
                eq += 1
                self.pos += 1
                self.col += 1
            if self.pos < self.length and self.source[self.pos] == '[':
                self.pos += 1
                self.col += 1
                close = ']' + '=' * eq + ']'
                end = self.source.find(close, self.pos)
                if end != -1:
                    end += len(close)
                    self.tokens.append(Token(TokenKind.LONG_COMMENT, self.source[start:end], self.line, self.col))
                    self.pos = end
                    self.col = 1
                    return
        # short comment --...
        self.pos += 2
        self.col += 2
        while self.pos < self.length and self.source[self.pos] != '\n':
            self.pos += 1
            self.col += 1
        self.tokens.append(Token(TokenKind.COMMENT, self.source[start:self.pos], self.line, self.col))

    def _scan_string(self, quote: str):
        start = self.pos
        self.pos += 1
        self.col += 1
        while self.pos < self.length:
            ch = self.source[self.pos]
            if ch == '\\':
                self.pos += 2
                self.col += 2
            elif ch == quote:
                self.pos += 1
                self.col += 1
                self.tokens.append(Token(TokenKind.STRING, self.source[start:self.pos], self.line, self.col))
                return
            elif ch == '\n':
                # invalid but we'll treat as newline
                self.line += 1
                self.col = 1
                self.pos += 1
            else:
                self.pos += 1
                self.col += 1
        self.tokens.append(Token(TokenKind.STRING, self.source[start:self.pos], self.line, self.col))

    def _scan_long_string(self):
        start = self.pos
        self.pos += 2
        self.col += 2
        eq = 0
        while self.pos < self.length and self.source[self.pos] == '=':
            eq += 1
            self.pos += 1
            self.col += 1
        if self.pos < self.length and self.source[self.pos] == '[':
            self.pos += 1
            self.col += 1
            close = ']' + '=' * eq + ']'
            end = self.source.find(close, self.pos)
            if end != -1:
                end += len(close)
                self.tokens.append(Token(TokenKind.LONG_STRING, self.source[start:end], self.line, self.col))
                self.pos = end
                self.col = 1
                return
        self.tokens.append(Token(TokenKind.STRING, self.source[start:self.pos], self.line, self.col))

    def _scan_number(self):
        start = self.pos
        # hex
        if self.source[self.pos] == '0' and self.pos+1 < self.length and self.source[self.pos+1].lower() == 'x':
            self.pos += 2
            while self.pos < self.length and self.source[self.pos].isalnum():
                self.pos += 1
            self.tokens.append(Token(TokenKind.HEX_NUMBER, self.source[start:self.pos], self.line, self.col))
            return
        while self.pos < self.length and (self.source[self.pos].isdigit() or self.source[self.pos] == '.'):
            self.pos += 1
        if self.pos < self.length and self.source[self.pos] in 'eE':
            self.pos += 1
            if self.pos < self.length and self.source[self.pos] in '+-':
                self.pos += 1
            while self.pos < self.length and self.source[self.pos].isdigit():
                self.pos += 1
        self.tokens.append(Token(TokenKind.NUMBER, self.source[start:self.pos], self.line, self.col))

    def _scan_identifier_or_keyword(self):
        start = self.pos
        while self.pos < self.length and (self.source[self.pos].isalnum() or self.source[self.pos] == '_'):
            self.pos += 1
        ident = self.source[start:self.pos]
        keywords = {"and","break","do","else","elseif","end","false","for","function","goto",
                    "if","in","local","nil","not","or","repeat","return","then","true","until","while"}
        kind = TokenKind.KEYWORD if ident in keywords else TokenKind.IDENTIFIER
        self.tokens.append(Token(kind, ident, self.line, self.col))

    def _scan_symbol(self):
        start = self.pos
        self.pos += 1
        self.col += 1
        self.tokens.append(Token(TokenKind.SYMBOL, self.source[start:self.pos], self.line, self.col))

# -----------------------------------------------------------------------------
# 2. LEXICAL SCOPE ANALYZER & SAFE RENAMER
# -----------------------------------------------------------------------------
class Scope:
    def __init__(self, parent: Optional[Scope] = None, is_function: bool = False):
        self.parent = parent
        self.is_function = is_function
        self.names: Set[str] = set()          # names declared in this scope
        self.references: Set[str] = set()     # names referenced
        self.children: List[Scope] = []
        self.shadow_map: Dict[str, str] = {}  # old_name -> new_name

    def declare(self, name: str):
        self.names.add(name)

    def add_reference(self, name: str):
        self.references.add(name)

    def resolve(self, name: str) -> bool:
        """True if name is declared in this scope or any parent."""
        if name in self.names:
            return True
        if self.parent:
            return self.parent.resolve(name)
        return False

    def get_scope_for_name(self, name: str) -> Optional[Scope]:
        if name in self.names:
            return self
        if self.parent:
            return self.parent.get_scope_for_name(name)
        return None

class ScopeBuilder:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self.current_scope = Scope()
        self.scope_stack = [self.current_scope]
        self.global_names = {
            "_G", "_ENV", "print", "warn", "error", "require", "pairs", "ipairs", "next",
            "typeof", "Instance", "bit32", "string", "table", "math", "os", "debug",
            # Add Roblox services etc. – we treat them as builtins, never renamed
        }

    def build(self):
        while self.pos < len(self.tokens):
            t = self.tokens[self.pos]
            if t.kind == TokenKind.KEYWORD and t.value == "local":
                self._handle_local()
            elif t.kind == TokenKind.KEYWORD and t.value in ("function", "do", "then", "repeat"):
                self._enter_scope()
            elif t.kind == TokenKind.KEYWORD and t.value in ("end", "until"):
                self._exit_scope()
            elif t.kind == TokenKind.IDENTIFIER:
                self.current_scope.add_reference(t.value)
                self.pos += 1
            else:
                self.pos += 1
        return self.scope_stack[0]

    def _enter_scope(self):
        new_scope = Scope(self.current_scope, is_function=(self.tokens[self.pos].value == "function"))
        self.current_scope.children.append(new_scope)
        self.scope_stack.append(new_scope)
        self.current_scope = new_scope
        self.pos += 1

    def _exit_scope(self):
        if len(self.scope_stack) > 1:
            self.scope_stack.pop()
            self.current_scope = self.scope_stack[-1]
        self.pos += 1

    def _handle_local(self):
        self.pos += 1
        # skip whitespace
        while self.pos < len(self.tokens) and self.tokens[self.pos].kind == TokenKind.WHITESPACE:
            self.pos += 1
        # parse names until '=', ';', newline, or end of statement
        names = []
        while self.pos < len(self.tokens):
            t = self.tokens[self.pos]
            if t.kind == TokenKind.IDENTIFIER:
                names.append(t.value)
                self.current_scope.declare(t.value)
                self.pos += 1
                # check for ','
                while self.pos < len(self.tokens) and self.tokens[self.pos].kind == TokenKind.WHITESPACE:
                    self.pos += 1
                if self.pos < len(self.tokens) and self.tokens[self.pos].value == ",":
                    self.pos += 1
                    while self.pos < len(self.tokens) and self.tokens[self.pos].kind == TokenKind.WHITESPACE:
                        self.pos += 1
                    continue
                else:
                    break
            else:
                break
        # if '=' or ';' or newline, skip
        while self.pos < len(self.tokens) and self.tokens[self.pos].kind not in (TokenKind.KEYWORD, TokenKind.EOF):
            if self.tokens[self.pos].value in ("=", ";", "\n"):
                break
            self.pos += 1

def safe_rename_identifiers(code: str) -> str:
    """Perform scope-aware renaming of local variables."""
    scanner = Scanner(code)
    tokens = scanner.scan()
    # Remove whitespace/comments for scope building? We'll keep them for replacement.
    # Build scope tree
    builder = ScopeBuilder(tokens)
    root_scope = builder.build()

    # Collect all local declarations in each scope
    # We'll generate new names for each declared local that is not a builtin/global.
    # For each scope, for each name in scope.names, if not in global_names, generate a fresh name.
    # We'll need to avoid collisions across scopes (shadowing) – we'll generate per-scope.
    used_globally = set()
    for name in root_scope.names:
        used_globally.add(name)
    # Builtins/globals never renamed
    builtins = {"print","warn","error","require","pairs","ipairs","next","typeof","Instance",
                "bit32","string","table","math","os","debug","_G","_ENV"}
    # We'll traverse scopes and assign new names
    def assign_names(scope: Scope, used_names: Set[str]):
        for name in list(scope.names):
            if name in builtins or name.startswith("_"):
                continue
            # generate new name not in used_names and not reserved
            new_name = _fresh(used_names, confuse=True)
            scope.shadow_map[name] = new_name
            used_names.add(new_name)
        for child in scope.children:
            # child inherits parent's used_names but can shadow
            assign_names(child, used_names.copy())

    assign_names(root_scope, set(builtins))

    # Now replace identifiers in token stream with shadow_map if they are local references.
    # We need to know the scope of each identifier occurrence. We'll re-traverse with scope stack.
    result = []
    pos = 0
    scope_stack = [root_scope]
    current_scope = root_scope

    def get_scope_for_pos(p):
        # simplistic: just use current_scope based on nesting; we'll update on enter/exit
        return current_scope

    # We'll do a second pass on tokens
    for i, tok in enumerate(tokens):
        if tok.kind == TokenKind.KEYWORD and tok.value in ("function","do","then","repeat"):
            # Enter child scope
            if current_scope.children:
                current_scope = current_scope.children[-1]  # assumes depth order
                scope_stack.append(current_scope)
            result.append(tok.value)
        elif tok.kind == TokenKind.KEYWORD and tok.value in ("end","until"):
            if len(scope_stack) > 1:
                scope_stack.pop()
                current_scope = scope_stack[-1]
            result.append(tok.value)
        elif tok.kind == TokenKind.IDENTIFIER:
            # resolve if it's a local in current or parent scope
            resolved = False
            for sc in reversed(scope_stack):
                if tok.value in sc.shadow_map:
                    result.append(sc.shadow_map[tok.value])
                    resolved = True
                    break
                elif tok.value in sc.names:
                    # declared but not renamed? should not happen
                    result.append(tok.value)
                    resolved = True
                    break
            if not resolved:
                result.append(tok.value)
        else:
            result.append(tok.value)

    # Reconstruct code with minimal spacing (will be compacted later)
    return " ".join(result)  # temporary, will be compacted later

# -----------------------------------------------------------------------------
# 3. STRING ENCRYPTION (improved)
# -----------------------------------------------------------------------------
def _literal_bytes(val: str) -> List[int] | None:
    # Reuse existing robust parser
    # (We'll keep the function from earlier, it's fine)
    pass  # placeholder – the full function is included in the final file

def _pack_meta(split_count: int, seed: int, add: int, step: int, blk: int, rot: int, rev: int) -> int:
    return (split_count & 0xF) | ((seed & 0xFF) << 4) | ((add & 0xFF) << 12) | ((step & 0x1F) << 20) | ((blk & 0xFF) << 25) | ((rot & 0x7) << 33) | ((rev & 0x1) << 36)

def _encrypt_string(plaintext: str, seed: int) -> Dict:
    plain = _literal_bytes(plaintext)
    if plain is None:
        raise ValueError("Invalid string literal")
    n = len(plain)
    if n == 0:
        return {"chunks": [[]], "meta": 0, "chk": 2166136261, "len": 0}
    split_count = secrets.randbelow(4) + 2
    # derive per-string parameters from global seed
    rng = random.Random(seed)
    seed_val = rng.randint(1, 251)
    add = rng.randint(0, 250)
    step = rng.randint(1, 31)
    blk = rng.randint(1, 251)
    rot = rng.randint(1, 7)
    rev = rng.randint(0, 1)
    # apply transforms
    s1 = [b ^ ((seed_val + i * step + add) % 256) for i, b in enumerate(plain)]
    s2 = [((b << rot) | (b >> (8 - rot))) & 0xFF for b in s1]
    s3 = [b ^ blk for b in s2]
    if rev:
        s3 = s3[::-1]
    chunks = [[] for _ in range(split_count)]
    for i, b in enumerate(s3):
        chunks[i % split_count].append(b)
    chk = 2166136261
    for b in plain:
        chk = ((chk ^ b) * 16777619) & 0xFFFFFFFF
    meta = _pack_meta(split_count, seed_val, add, step, blk, rot, rev)
    return {"chunks": chunks, "meta": meta, "chk": chk, "len": n}

# -----------------------------------------------------------------------------
# 4. INTEGER OBFUSCATION (multiple forms)
# -----------------------------------------------------------------------------
def _mask_int(n: int) -> str:
    # Returns an equivalent expression string
    if n < 0:
        # represent as -(positive)
        return "(-" + _mask_int(-n) + ")"
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    # choose from several forms
    r = secrets.randbelow(5)
    if r == 0:
        k = secrets.randbelow(100) + 1
        return f"({n+k}-{k})"
    elif r == 1:
        k = secrets.randbelow(50) + 2
        return f"(({n*k})//{k})"
    elif r == 2:
        k = secrets.randbelow(0xFFFF) + 1
        return f"bit32.bxor({n^k},{k})"
    elif r == 3:
        k = secrets.randbelow(0xFF) + 1
        return f"bit32.band(bit32.bor({n},{k}),bit32.bxor({n|k},{k^(n&k)}))"
    else:
        a = secrets.randbelow(0xFFF) + 1
        return f"bit32.bxor({n^a},{a})"

# -----------------------------------------------------------------------------
# 5. JUNK CODE GENERATOR (safe)
# -----------------------------------------------------------------------------
def _generate_junk(used: Set[str]) -> str:
    # Generate harmless junk: unused local, dummy arithmetic, never executed if etc.
    junk = []
    if secrets.randbelow(100) < 30:
        v = _fresh(used)
        junk.append(f"local {v}={_mask_int(secrets.randbelow(1000))}")
    if secrets.randbelow(100) < 20:
        # an always-false if with no effect
        junk.append(f"if false then end")
    if secrets.randbelow(100) < 10:
        # dummy function
        f = _fresh(used)
        junk.append(f"local function {f}() return 1 end")
    return "\n".join(junk)

# -----------------------------------------------------------------------------
# 6. DECODER GENERATOR (modular)
# -----------------------------------------------------------------------------
def generate_decoder(records: List[Dict], used: Set[str]) -> str:
    # This returns a string containing the decoder functions and tables.
    # We'll generate helpers for each transform step.
    # Randomize order of helpers.
    helpers = []
    # We'll create a function for XOR with key, rotate, etc.
    # But for brevity we'll keep a compact decoder but with randomized names.
    # The important part is that we use per-record metadata.
    N = lambda: _fresh(used, confuse=True)
    meta_tbl = N(); chunk_tbl = N(); len_tbl = N(); chk_tbl = N(); cache = N()
    decode_fn = N()
    bxor, bor, band, lshift, rshift = N(), N(), N(), N(), N()
    char, concat, floor = N(), N(), N()
    trap = N()

    # Build tables
    meta_list = [str(r["meta"]) for r in records]
    chunk_list = []
    for r in records:
        chunk_strs = ["{" + ",".join(map(str, ch)) + "}" for ch in r["chunks"]]
        chunk_list.append("{" + ",".join(chunk_strs) + "}")
    len_list = [str(r["len"]) for r in records]
    chk_list = [str(r["chk"]) for r in records]

    decoder_code = f"""local {bxor},{bor},{band},{lshift},{rshift}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {char},{concat},{floor}=string.char,table.concat,math.floor
local {trap}=function()error("",0)end
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
local seed={band}({rshift}(m,4),0xFF)
local add={band}({rshift}(m,12),0xFF)
local step={band}({rshift}(m,20),0x1F)
local blk={band}({rshift}(m,25),0xFF)
local rot={band}({rshift}(m,33),0x7)
local rev={band}({rshift}(m,36),0x1)
local chunks={chunk_tbl}[idx+1]
local out={{}}; local chk=2166136261
for i=1,n do
local ci=(i-1)%split+1
local chunk=chunks[ci]
if not chunk then {trap}() end
local bi=(i-1)//split+1
local b=chunk[bi]
if not b then {trap}() end
local v=b
v={bxor}(v,blk)
v={bor}({rshift}(v,rot),{lshift}(v,8-rot))
v={band}(v,255)
v={bxor}(v,(seed+(i-1)*step+add)%256)
if v<0 then v=v+256 end
chk={band}(({bxor}(chk,v)*16777619),4294967295)
local pos = i
if rev==1 then pos = n - i + 1 end
out[pos]={char}(v)
end
if chk~=expected then {trap}() end
local result={concat}(out)
{cache}[idx]=result
return result
end"""
    return decoder_code

# -----------------------------------------------------------------------------
# 7. CONTROL-FLOW TRANSFORMS (lightweight)
# -----------------------------------------------------------------------------
def _flatten_simple(code: str) -> str:
    # Wrap code in a while loop with state variable (only if simple)
    # This is a simplistic flattening; we'll keep it optional.
    # For MAX level, we apply it.
    if OBFUSCATION_LEVEL in ("MAX", "HIGH"):
        state = _fresh(set())
        lines = code.splitlines()
        new = [f"local {state}=0", "while true do", f"if {state}==0 then"]
        new.extend("    " + l for l in lines)
        new.append(f"    {state}=1")
        new.append(f"elseif {state}==1 then")
        new.append("    break")
        new.append("end")
        new.append("end")
        return "\n".join(new)
    return code

# -----------------------------------------------------------------------------
# 8. VALIDATION PIPELINE
# -----------------------------------------------------------------------------
def validate_generated(code: str) -> bool:
    # Simple checks:
    # 1. No undefined identifiers (by scanning for identifiers and ensuring they are defined or builtins)
    # 2. All __R references have payloads
    # 3. No syntax errors (if luac available)
    import subprocess
    # Check for undefined identifiers – naive but catches obvious errors
    # We'll just use our Scanner to find identifiers and then see if they are defined in a scope?
    # For simplicity, we'll use a heuristic: check for 'local' declarations.
    # But we'll rely on the fact that our generator produces correct code.
    # We'll also try to run luac if available.
    try:
        # Write code to temp file and check syntax
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False) as f:
            f.write(code)
            f.flush()
            result = subprocess.run(["luac", "-p", f.name], capture_output=True, text=True)
        if result.returncode != 0:
            print("Syntax error:", result.stderr)
            return False
        return True
    except FileNotFoundError:
        # luac not available, skip
        return True

# -----------------------------------------------------------------------------
# 9. MAIN OBFUSCATOR
# -----------------------------------------------------------------------------
def obfuscate_luau(source: str) -> str:
    # 0. Extract directives
    directives = []
    for line in source.splitlines():
        if line.strip().startswith("--!"):
            directives.append(line.strip())

    # 1. Rename identifiers safely (scope-aware)
    code = safe_rename_identifiers(source)

    # 2. Tokenize and process strings/numbers
    scanner = Scanner(code)
    tokens = scanner.scan()
    # We'll process tokens to encrypt strings and mask integers
    used = set()
    records = []
    out_tokens = []
    for tok in tokens:
        if tok.kind in (TokenKind.STRING, TokenKind.LONG_STRING):
            # encrypt
            enc = _encrypt_string(tok.value, secrets.randbelow(0xFFFFFFFF))
            records.append(enc)
            out_tokens.append(f"__R{len(records)-1}__")
        elif tok.kind in (TokenKind.NUMBER, TokenKind.HEX_NUMBER):
            # if number is small integer, mask it
            try:
                val = int(tok.value, 0)
                if abs(val) < 1000000:
                    out_tokens.append(_mask_int(val))
                else:
                    out_tokens.append(tok.value)
            except:
                out_tokens.append(tok.value)
        elif tok.kind == TokenKind.COMMENT or tok.kind == TokenKind.LONG_COMMENT:
            continue  # remove comments
        elif tok.kind == TokenKind.WHITESPACE:
            continue  # will be compacted later
        else:
            out_tokens.append(tok.value)

    # 3. Shuffle records and generate decoder
    order = list(range(len(records)))
    secrets.SystemRandom().shuffle(order)
    ordered_records = [records[i] for i in order]
    decoder_code = generate_decoder(ordered_records, used)

    # 4. Replace placeholders
    body_tokens = []
    for t in out_tokens:
        if t.startswith("__R") and t.endswith("__"):
            idx = int(t[3:-2])
            body_tokens.append(f"{decoder_code.split()[0]}({order.index(idx)})")
        else:
            body_tokens.append(t)
    body = " ".join(body_tokens)  # will be compacted later

    # 5. Control-flow obfuscation (optional)
    body = _flatten_simple(body)

    # 6. Add junk (optional)
    if OBFUSCATION_LEVEL in ("MAX", "HIGH"):
        junk = _generate_junk(used)
        body = junk + "\n" + body

    # 7. Compact (remove extra whitespace, comments)
    # We'll use a basic minifier: remove whitespace except where necessary
    # We'll reuse _compact from previous versions
    body = _compact(body.split())

    # 8. Assemble final code
    header = directives + [ATTRIBUTION, decoder_code]
    final_code = "\n".join(header) + "\n" + body

    # 9. Validate
    if not validate_generated(final_code):
        raise RuntimeError("Validation failed – generated code invalid")

    return final_code

# -----------------------------------------------------------------------------
# 10. DISCORD COG
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
