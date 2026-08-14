from __future__ import annotations

import asyncio
import io
import re
import secrets
import random
import base64
from pathlib import Path
from typing import Iterable, Set, Dict, Tuple, List, Any

import discord
from discord import app_commands
from discord.ext import commands

# Optional luaparser for AST encryption; fallback to token‑based
try:
    from luaparser import ast, astnodes
    from luaparser.parser import Parser
    HAS_LUAPARSER = True
except ImportError:
    HAS_LUAPARSER = False

MAX_SOURCE_BYTES = 750_000
ATTRIBUTION = "-- obfuscated by buterfuscate v8"

# ── Tokeniser ──────────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(
    r"""
    (?P<long_comment>--\[(?:=*)\[.*?\](?:=*)\])
    |(?P<comment>--[^\r\n]*)
    |(?P<whitespace>\s+)
    |(?P<long_string>\[(?:=*)\[.*?\](?:=*)\])
    |(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
    |(?P<number>0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)
    |(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<symbol>.)
    """,
    re.DOTALL | re.VERBOSE,
)
_CONFUSE = "IlO0"
_ALNUM   = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_STARTS  = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"

def _fresh(used: set[str], *, confuse: bool = False) -> str:
    pool = (_CONFUSE * 6 + _ALNUM) if confuse else _ALNUM
    while True:
        n = secrets.randbelow(7) + 9
        name = secrets.choice(_STARTS) + "".join(secrets.choice(pool) for _ in range(n))
        if name not in used:
            used.add(name)
            return name

def _tokens(src: str) -> Iterable[tuple[str, str]]:
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            yield "symbol", src[pos]; pos += 1; continue
        pos = m.end()
        yield m.lastgroup or "symbol", m.group(0)

def _literal_bytes(val: str) -> list[int] | None:
    if val.startswith("["):
        m = re.match(r"^\[(=*)\[(.*)\]\1\]$", val, re.DOTALL)
        return list(m.group(2).encode()) if m else None
    if len(val) < 2 or val[0] not in {'"', "'"} or val[-1] != val[0]:
        return None
    body = val[1:-1]; result: list[int] = []; i = 0
    esc = {"a":7,"b":8,"f":12,"n":10,"r":13,"t":9,"v":11,"\\": 92,"'":39,'"':34}
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            result.extend(ch.encode()); i += 1; continue
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
            while c < len(body) and len(d)<3 and body[c].isdigit():
                d += body[c]; c += 1
            n = int(d)
            if n > 255: return None
            result.append(n); i = c; continue
        if e in {"\n","\r"}:
            if e=="\r" and i+1<len(body) and body[i+1]=="\n": i+=1
            result.append(10); i += 1; continue
        return None
    return result

def _mba(n: int) -> str:
    n &= 0xFFFFFFFF
    s = secrets.randbelow(6)
    if s==0:
        k=secrets.randbelow(0x7FFF)+1; return f"bit32.bxor({n^k},{k})"
    if s==1:
        k=secrets.randbelow(200)+5; return f"({n+k}-{k})"
    if s==2:
        k=secrets.randbelow(50)+2; return f"(({n*k})//{k})"
    if s==3:
        k=secrets.randbelow(0xFF)+1
        return f"bit32.band(bit32.bor({n},{k}),bit32.bxor({n|k},{k^(n&k)}))"
    if s==4:
        r=secrets.randbelow(7)+1
        rot=((n<<r)|(n>>(32-r)))&0xFFFFFFFF
        return f"bit32.bor(bit32.rshift({rot},{r}),bit32.lshift({rot},32-{r}))"
    a=secrets.randbelow(0xFFF)+1; return f"bit32.bxor({n^a},{a})"

def _opaque() -> str:
    s=secrets.randbelow(4)
    if s==0:
        x=secrets.randbelow(200)+5; return f"(bit32.bxor({x},{x})==0)"
    if s==1:
        x=secrets.randbelow(100)+1; return f"(bit32.band({x},0)==0)"
    if s==2:
        x=secrets.randbelow(50)+2; return f"(({x*2})//{x}==2)"
    x=secrets.randbelow(0xFF)+1; return f"(bit32.bor({x},0)=={x})"

def _mask_num(val: str) -> str:
    if not re.fullmatch(r"\d+", val) or len(val)>7: return val
    n=int(val)
    if n>9_999_999: return val
    return _mba(n)

def _pack_meta(split_count: int, seed: int, add: int, step: int, blk: int, rot: int, rev: int) -> int:
    return (split_count & 0xF) | ((seed & 0xFF) << 4) | ((add & 0xFF) << 12) | ((step & 0x1F) << 20) | ((blk & 0xFF) << 25) | ((rot & 0x7) << 33) | ((rev & 0x1) << 36)

def _encrypt_variable(val: str) -> dict | None:
    plain = _literal_bytes(val)
    if plain is None:
        return None
    n = len(plain)
    if n == 0:
        return {"chunks": [[]], "meta": 0, "chk": 2166136261}
    split_count = secrets.randbelow(4) + 2  # 2-5
    seed = secrets.randbelow(251) + 1
    add = secrets.randbelow(251)
    step = secrets.randbelow(31) + 1
    blk = secrets.randbelow(251) + 1
    rot = secrets.randbelow(7) + 1
    rev = secrets.randbelow(2)
    # apply transforms
    s1 = [b ^ ((seed + i * step + add) % 256) for i, b in enumerate(plain)]
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
    meta = _pack_meta(split_count, seed, add, step, blk, rot, rev)
    return {"chunks": chunks, "meta": meta, "chk": chk}

def _compact(tokens: list[str]) -> str:
    out = []
    for t in tokens:
        if out and _sep(out[-1], t):
            out.append(" ")
        out.append(t)
    return "".join(out)

def _sep(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if (a[-1].isalnum() or a[-1] == "_") and (b[0].isalnum() or b[0] == "_"):
        return True
    return a.endswith("-") and b.startswith("-")

def _rename_locals(parsed: list[tuple[str, str]], used: set[str]) -> list[tuple[str, str]]:
    sig = [(i, k, v) for i, (k, v) in enumerate(parsed)
           if k not in {"whitespace", "comment", "long_comment"}]
    decls: set[str] = set()
    depth = 0
    for idx, (_, kind, val) in enumerate(sig):
        if val == "local" and depth == 0:
            nxt = idx + 1
            if nxt < len(sig) and sig[nxt][2] == "function":
                nxt += 1
                if nxt < len(sig) and sig[nxt][1] == "identifier":
                    decls.add(sig[nxt][2])
            else:
                expect = True
                while nxt < len(sig):
                    _, nk, nv = sig[nxt]
                    if nv in {"=", ";"}:
                        break
                    if expect and nk == "identifier":
                        decls.add(nv)
                        expect = False
                    elif nv == ",":
                        expect = True
                    nxt += 1
        if val in {"function", "do", "then", "repeat"}:
            depth += 1
        elif val in {"end", "until"}:
            depth = max(0, depth - 1)
    if not decls:
        return parsed
    rmap = {n: _fresh(used, confuse=True) for n in sorted(decls)}
    prev_s = []
    prev = ""
    for k, v in parsed:
        prev_s.append(prev)
        if k not in {"whitespace", "comment", "long_comment"}:
            prev = v
    nxt_s = [""] * len(parsed)
    fol = ""
    for i in range(len(parsed) - 1, -1, -1):
        nxt_s[i] = fol
        k, v = parsed[i]
        if k not in {"whitespace", "comment", "long_comment"}:
            fol = v
    out = []
    bd = 0
    for i, (kind, val) in enumerate(parsed):
        if kind == "symbol":
            if val == "{":
                bd += 1
            elif val == "}":
                bd = max(0, bd - 1)
        if kind != "identifier" or val not in rmap:
            out.append((kind, val))
            continue
        is_field = (prev_s[i] in {".", ":"}) or (bd > 0 and nxt_s[i] == "=")
        out.append((kind, val if is_field else rmap[val]))
    return out

def _rename_builtins(code: str, used: set[str]) -> str:
    # Simple builtin renaming: find calls to known builtins and replace them with aliases.
    builtins = {
        "print", "assert", "error", "pcall", "xpcall", "type", "tostring", "tonumber",
        "select", "next", "rawget", "rawset", "rawequal", "setmetatable", "getmetatable",
        "loadstring", "string.byte", "string.char", "string.find", "string.format",
        "string.gmatch", "string.gsub", "string.len", "string.lower", "string.match",
        "string.rep", "string.reverse", "string.sub", "string.upper",
        "table.insert", "table.remove", "table.sort", "table.concat",
        "math.abs", "math.acos", "math.asin", "math.atan", "math.ceil", "math.cos",
        "math.deg", "math.exp", "math.floor", "math.fmod", "math.max", "math.min",
        "math.modf", "math.rad", "math.sin", "math.sqrt", "math.tan",
        "os.clock", "os.date", "os.difftime", "os.time"
    }
    # We'll tokenize, find identifiers that are simple names of builtins (not field access),
    # and replace them with aliases.
    tokens = list(_tokens(code))
    # First, find which builtins are used as non‑field identifiers.
    used_builtins = set()
    prev = ""
    for kind, val in tokens:
        if kind == "identifier" and val in builtins:
            # check if it's a field (preceded by '.' or ':')
            if prev not in {".", ":"}:
                used_builtins.add(val)
        if kind not in {"whitespace", "comment", "long_comment"}:
            prev = val
    if not used_builtins:
        return code
    # Generate aliases
    alias_map = {}
    for b in used_builtins:
        alias_map[b] = _fresh(used, confuse=True)
    # Replace in token stream with context (field protection)
    prev = ""
    out_tokens = []
    for kind, val in tokens:
        if kind == "identifier" and val in alias_map and prev not in {".", ":"}:
            out_tokens.append(("identifier", alias_map[val]))
        else:
            out_tokens.append((kind, val))
        if kind not in {"whitespace", "comment", "long_comment"}:
            prev = val
    new_code = _compact([v for k, v in out_tokens])
    # Prepend local declarations
    decl_parts = []
    assign_parts = []
    for alias, original in alias_map.items():
        decl_parts.append(alias)
        assign_parts.append(f"{alias}={original}")
    declaration = "local " + ",".join(decl_parts) + "\n" + ";".join(assign_parts) + ";\n"
    return declaration + new_code

def _flatten_control_flow(code: str, used: set[str]) -> str:
    state_var = _fresh(used)
    new_body = [f"local {state_var}=0", "while true do", f"if {state_var}==0 then"]
    for line in code.splitlines():
        new_body.append("    " + line)
    new_body.append(f"    {state_var}=1")
    new_body.append(f"elseif {state_var}==1 then")
    new_body.append("    break")
    new_body.append("end")
    new_body.append("end")
    return "\n".join(new_body)

def _insert_junk(code: str, used: set[str]) -> str:
    lines = code.splitlines()
    new_lines = []
    for line in lines:
        new_lines.append(line)
        if secrets.randbelow(100) < 20:
            dummy = _fresh(used)
            junk = f"{dummy}=({_mba(secrets.randbelow(0xFFFF))})"
            new_lines.append(junk)
        if secrets.randbelow(100) < 10:
            pred = _opaque()
            if secrets.randbelow(2) == 0:
                new_lines.append(f"if {pred} then end")
            else:
                new_lines.append(f"if not ({pred}) then end")
    return "\n".join(new_lines)

def _minify(code: str) -> str:
    toks = list(_tokens(code))
    filtered = [(k, v) for k, v in toks if k not in ("whitespace", "comment", "long_comment")]
    return _compact([v for k, v in filtered])

def obfuscate_luau(source: str) -> str:
    # Step 1: preserve directives
    directives = []
    for line in source.splitlines():
        if line.strip().startswith("--!"):
            directives.append(line.strip())

    # Step 2: rename locals and builtins
    used = set()
    tokens = list(_tokens(source))
    tokens = _rename_locals(tokens, used)
    source = _compact([v for k, v in tokens])
    source = _rename_builtins(source, used)

    # Step 3: encrypt strings with variable split
    token_stream = list(_tokens(source))
    records = []
    out_tok = []
    for kind, val in token_stream:
        if kind in ("long_string", "string"):
            enc = _encrypt_variable(val)
            if enc is None:
                out_tok.append(val)
            else:
                records.append(enc)
                out_tok.append(f"__R{len(records)-1}__")
        elif kind == "number":
            out_tok.append(_mask_num(val))
        elif kind not in ("whitespace", "comment", "long_comment"):
            out_tok.append(val)
    body = _compact(out_tok)

    # Step 4: shuffle records
    order = list(range(len(records)))
    secrets.SystemRandom().shuffle(order)
    ordered_records = [records[i] for i in order]
    # Build chunk and meta tables
    chunk_tables = []
    meta_list = []
    for rec in ordered_records:
        chunks = rec["chunks"]
        chunk_strs = ["{" + ",".join(map(str, ch)) + "}" for ch in chunks]
        chunk_tables.append("{" + ",".join(chunk_strs) + "}")
        meta_list.append(str(rec["meta"]))

    # Step 5: generate decoder runtime
    used_runtime = set()
    N = lambda: _fresh(used_runtime, confuse=True)

    decoder = N()
    cache = N()
    meta_tbl = N()
    chunk_tbl = N()
    bxor, bor, band, lshift, rshift = N(), N(), N(), N(), N()
    char, concat, floor, type_fn = N(), N(), N(), N()
    trap = N()

    # Build decoder function with variable split handling
    # We'll generate code that extracts split count, then iterates over chunks.
    decoder_code = f"""local {bxor},{bor},{band},{lshift},{rshift}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {char},{concat},{floor},{type_fn}=string.char,table.concat,math.floor,type
local {trap}=function()error("",0)end
local {meta_tbl}={{{",".join(meta_list)}}}
local {chunk_tbl}={{{",".join(chunk_tables)}}}
local {cache}={{}}
local {decoder}=function(idx)
if {cache}[idx] then return {cache}[idx] end
local m={meta_tbl}[idx+1]
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
local pos=1
for i=1,{floor}((#chunks)*1000) do
local ci=(i-1)%split+1
local chunk=chunks[ci]
if not chunk then break end
local bi=(i-1)//split+1
local b=chunk[bi]
if not b then break end
local v=b
v={bxor}(v,blk)
v={bor}({rshift}(v,rot),{lshift}(v,8-rot))
v={band}(v,255)
v={bxor}(v,(seed+(i-1)*step+add)%256)
if v<0 then v=v+256 end
chk={band}(({bxor}(chk,v)*16777619),4294967295)
out[i]={char}(v)
end
if chk~={_mba(ordered_records[0]['chk'])} then {trap}() end
local result={concat}(out)
{cache}[idx]=result
return result
end"""

    # Replace placeholders in body
    for i, t in enumerate(out_tok):
        if t.startswith("__R") and t.endswith("__"):
            oi = int(t[3:-2])
            out_tok[i] = f"{decoder}({order.index(oi)})"
    body = _compact(out_tok)

    # Step 6: control‑flow flattening and junk
    body = _flatten_control_flow(body, used)
    body = _insert_junk(body, used)

    # Step 7: assemble final code and minify
    header = directives + [ATTRIBUTION, decoder_code]
    final_code = "\n".join(header) + "\n" + body + "\n"
    return _minify(final_code)

# ── Discord cog ──────────────────────────────────────────────────────────────
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
