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
ATTRIBUTION = "-- obfuscated by buterfuscate v7"

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

def _encrypt(val: str) -> dict | None:
    plain=_literal_bytes(val)
    if plain is None: return None
    n=len(plain)
    if n==0:
        return {"a":[],"b":[],"c":[],"seed":0,"add":0,"step":0,
                "blk":0,"rot":0,"rev":0,"n":0,"chk":2166136261}
    seed=secrets.randbelow(251)+1; add=secrets.randbelow(251)
    step=secrets.randbelow(31)+1;  blk=secrets.randbelow(251)+1
    rot=secrets.randbelow(7)+1;    rev=secrets.randbelow(2)
    s1=[b^((seed+i*step+add)%256) for i,b in enumerate(plain)]
    s2=[((b<<rot)|(b>>(8-rot)))&0xFF for b in s1]
    s3=[b^blk for b in s2]
    if rev: s3=s3[::-1]
    chk=2166136261
    for b in plain: chk=((chk^b)*16777619)&0xFFFFFFFF
    return {"a":s3[0::3],"b":s3[1::3],"c":s3[2::3],
            "seed":seed,"add":add,"step":step,"blk":blk,
            "rot":rot,"rev":rev,"n":n,"chk":chk}

OP_LOAD=0; OP_JUNK=1; _REAL_OPS=2

def _pack(op:int, operand:int, remap:list[int]) -> int:
    return (remap[op]&0xF)|((secrets.randbelow(4)&0x3)<<4)|((operand&0xFFFF)<<6)

def _sep(a:str,b:str)->bool:
    if not a or not b: return False
    if (a[-1].isalnum() or a[-1]=="_") and (b[0].isalnum() or b[0]=="_"): return True
    return a.endswith("-") and b.startswith("-")

def _compact(tokens:list[str])->str:
    out=[]
    for t in tokens:
        if out and _sep(out[-1],t): out.append(" ")
        out.append(t)
    return "".join(out)

def _rename_locals(parsed: list[tuple[str,str]], used: set[str]) -> list[tuple[str,str]]:
    sig = [(i,k,v) for i,(k,v) in enumerate(parsed)
           if k not in {"whitespace","comment","long_comment"}]
    decls: set[str] = set(); depth = 0
    for idx,(_,kind,val) in enumerate(sig):
        if val=="local" and depth==0:
            nxt = idx+1
            if nxt<len(sig) and sig[nxt][2]=="function":
                nxt+=1
                if nxt<len(sig) and sig[nxt][1]=="identifier": decls.add(sig[nxt][2])
            else:
                expect=True
                while nxt<len(sig):
                    _,nk,nv=sig[nxt]
                    if nv in {"=",";"}: break
                    if expect and nk=="identifier": decls.add(nv); expect=False
                    elif nv==",": expect=True
                    nxt+=1
        if val in {"function","do","then","repeat"}: depth+=1
        elif val in {"end","until"}: depth=max(0,depth-1)
    if not decls: return parsed
    rmap={n:_fresh(used,confuse=True) for n in sorted(decls)}
    prev_s=[]; prev=""
    for k,v in parsed:
        prev_s.append(prev)
        if k not in {"whitespace","comment","long_comment"}: prev=v
    nxt_s=[""]*len(parsed); fol=""
    for i in range(len(parsed)-1,-1,-1):
        nxt_s[i]=fol
        k,v=parsed[i]
        if k not in {"whitespace","comment","long_comment"}: fol=v
    out=[]; bd=0
    for i,(kind,val) in enumerate(parsed):
        if kind=="symbol":
            if val=="{": bd+=1
            elif val=="}": bd=max(0,bd-1)
        if kind!="identifier" or val not in rmap:
            out.append((kind,val)); continue
        is_field=(prev_s[i] in {".",":" } or (bd>0 and nxt_s[i]=="="))
        out.append((kind, val if is_field else rmap[val]))
    return out

def _output_name(filename: str) -> str:
    stem=re.sub(r"[^A-Za-z0-9_.-]+","_",Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.txt"

# ── StringEncrypter (AST‑based) ──────────────────────────────────────────────
class StringEncrypter:
    def __init__(self, source: str, str_key: int):
        self.source = source
        self.str_key = str_key
        self.b32_decryptor = (
            'local function a(b,c)local d={}for e=1,#b,c do table.insert(d,b:sub(e,e+c-1))end;return d end;'
            'local function f(g)local d=""repeat local h=g/2;local i,j=math.modf(h)g=i;d=math.ceil(j)..d until g==0;return d end;'
            'local k="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"'
            'local function Base32(b)local m=b:gsub(".",function(n)if n=="="then return""end;local o=string.find(k,n)o=o-1;return string.format("%05u",f(o))end)'
            'local p=a(m,8)local q={}for r,s in pairs(p)do table.insert(q,string.char(tonumber(s,2)))end;'
            'local t=table.concat(q)local u={}for e=1,#t,1 do local s=string.byte(t,e)table.insert(u,e,s)end;'
            'local v=""for e=1,#u-1,1 do local s=u[e]local w=DecryptSTR(s)v=v..string.char(w)end;return v end\n'
        )
        self.str_decryptor = (
            "local function DecryptSTR(b)local c,d=1,0;local e={};while b>0 and e>0 do local f,g=b%2,e%2;"
            "if f~=g then d=d+c end;b=(b-f)/2;e=(e-g)/2;c=c*2 end;"
            "if b<e then b=e end;while b>0 do local f=b%2;if f>0 then d=d+c end;b=(b-f)/2;c=c*2 end; return d end;\n"
            + self.b32_decryptor
        )

    @staticmethod
    def random_string(length: int) -> str:
        return ''.join(random.choice('qwertyuioplkjhgfdsazxcvbnmQWERTYUIOPLKJHGFDSAZXCVBNM') for _ in range(length))

    @staticmethod
    def encrypt(plaintext: str, key: int) -> str:
        pt_bytes = [ord(c) for c in plaintext.encode('utf-8')]
        for i in range(len(pt_bytes)):
            pt_bytes[i] ^= key
        return base64.b32encode(bytes(pt_bytes)).decode('utf-8')

    def apply(self) -> tuple[str, str]:
        if not HAS_LUAPARSER:
            return self.source, self.str_decryptor
        parser = Parser(self.source)
        tree = parser.parse()
        string_nodes = []
        class StringVisitor(ast.ASTVisitor):
            def visit_String(self, node):
                string_nodes.append(node)
        StringVisitor().visit(tree)
        local_names = []
        local_table = []
        replace_map = {}
        for node in string_nodes:
            if len(local_names) >= 100:
                break
            try:
                s = node.s
                if len(s) < 2:
                    continue
                rname = self.random_string(7)
                local_names.append(rname)
                encrypted = self.encrypt(s, self.str_key)
                local_table.append(f"local {rname} = Base32(\"{encrypted}\")\n")
                replace_map[s] = rname
            except:
                pass
        new_source = self.source
        for s, rname in replace_map.items():
            new_source = new_source.replace(f'"{s}"', f'({rname})')
            new_source = new_source.replace(f"'{s}'", f'({rname})')
        code = "".join(local_table) + new_source
        return code, self.str_decryptor

# ── MathEncrypter ─────────────────────────────────────────────────────────────
class MathEncrypter:
    def __init__(self, source: str, int_key: int):
        self.source = source
        self.int_key = int_key
        self.decrypt = f"local function DecryptINT(b)local c,d=1,0;local e={{}};while b>0 and e>0 do local f,g=b%2,e%2;if f~=g then d=d+c end;b=(b-f)/2;e=(e-g)/2;c=c*2 end;if b<e then b=e end;while b>0 do local f=b%2;if f>0 then d=d+c end;b=(b-f)/2;c=c*2 end; return d end\n"

    def apply(self) -> tuple[str, str]:
        if not HAS_LUAPARSER:
            return self.source, self.decrypt
        tokens = list(_tokens(self.source))
        new_tokens = []
        for kind, val in tokens:
            if kind == "number" and re.fullmatch(r"\d+", val) and int(val) < 2**31:
                val_int = int(val)
                xor_val = val_int ^ self.int_key
                new_tokens.append(("identifier", "DecryptINT"))
                new_tokens.append(("symbol", "("))
                new_tokens.append(("number", str(xor_val)))
                new_tokens.append(("symbol", ")"))
            else:
                new_tokens.append((kind, val))
        new_source = _compact([v for k, v in new_tokens])
        return new_source, self.decrypt

# ── VariableRenamer (safe, token‑based) ──────────────────────────────────────
BUILTINS = [
    "assert", "collectgarbage", "dofile", "error", "ipairs", "next",
    "pairs", "pcall", "print", "rawequal", "rawget", "rawlen", "rawset",
    "select", "tonumber", "tostring", "type", "unpack", "xpcall",
    "math.abs", "math.acos", "math.asin", "math.atan", "math.ceil",
    "math.cos", "math.deg", "math.exp", "math.floor", "math.fmod",
    "math.huge", "math.log", "math.max", "math.min", "math.modf",
    "math.pi", "math.pow", "math.rad", "math.random", "math.randomseed",
    "math.sin", "math.sqrt", "math.tan",
    "string.byte", "string.char", "string.dump", "string.find",
    "string.format", "string.gmatch", "string.gsub", "string.len",
    "string.lower", "string.match", "string.rep", "string.reverse",
    "string.sub", "string.upper",
    "table.concat", "table.insert", "table.remove", "table.sort",
    "table.pack", "table.unpack",
    "os.clock", "os.date", "os.difftime", "os.execute", "os.exit",
    "os.getenv", "os.remove", "os.rename", "os.setlocale", "os.time",
    "os.tmpname",
]

def _rename_variables(code: str, target: str = "luau") -> str:
    # Step 1: rename locals using existing robust function
    used = set()
    tokens = list(_tokens(code))
    tokens_renamed = _rename_locals(tokens, used)
    code_renamed = _compact([v for k, v in tokens_renamed])

    # Step 2: find builtins used as non‑field identifiers
    builtin_simple_names = {b.split(".")[-1] for b in BUILTINS}
    if target == "luau":
        builtin_simple_names.discard("type")
        builtin_simple_names.discard("dump")

    # Compute context for tokens_renamed (field detection)
    prev_s = [""] * len(tokens_renamed)
    prev = ""
    for i, (kind, val) in enumerate(tokens_renamed):
        prev_s[i] = prev
        if kind not in {"whitespace","comment","long_comment"}:
            prev = val
    nxt_s = [""] * len(tokens_renamed)
    fol = ""
    for i in range(len(tokens_renamed)-1, -1, -1):
        nxt_s[i] = fol
        kind, val = tokens_renamed[i]
        if kind not in {"whitespace","comment","long_comment"}:
            fol = val
    bd = 0
    builtin_used = set()
    for i, (kind, val) in enumerate(tokens_renamed):
        if kind == "symbol":
            if val == "{":
                bd += 1
            elif val == "}":
                bd = max(0, bd-1)
        if kind == "identifier":
            is_field = (prev_s[i] in {".", ":"}) or (bd > 0 and nxt_s[i] == "=")
            if not is_field and val in builtin_simple_names:
                builtin_used.add(val)

    if not builtin_used:
        return code_renamed

    # Step 3: generate aliases
    used_names = set(used)
    builtin_alias = {}
    for name in builtin_used:
        new_name = _fresh(used_names, confuse=True)
        builtin_alias[name] = new_name

    # Step 4: replace builtin identifiers in tokens_renamed
    output_tokens = []
    for i, (kind, val) in enumerate(tokens_renamed):
        if kind == "identifier":
            is_field = (prev_s[i] in {".", ":"}) or (bd > 0 and nxt_s[i] == "=")
            if not is_field and val in builtin_alias:
                output_tokens.append(("identifier", builtin_alias[val]))
                continue
        output_tokens.append((kind, val))
    final_code = _compact([v for k, v in output_tokens])

    # Step 5: prepend local declarations
    decl_parts = []
    assign_parts = []
    for alias, original in builtin_alias.items():
        decl_parts.append(alias)
        assign_parts.append(f"{alias}={original}")
    declaration = "local " + ",".join(decl_parts) + "\n" + ";".join(assign_parts) + ";\n"
    final_code = declaration + final_code

    return final_code

# ── Minify ────────────────────────────────────────────────────────────────────
def _minify(code: str) -> str:
    """Remove all unnecessary whitespace and comments, produce a single line."""
    toks = list(_tokens(code))
    # strip whitespace and comment tokens
    filtered = [(k, v) for k, v in toks if k not in ("whitespace", "comment", "long_comment")]
    # use _compact to add spaces only where needed
    return _compact([v for k, v in filtered])

# ── Main obfuscation ──────────────────────────────────────────────────────────
def obfuscate_luau(source: str) -> str:
    # Step 1: Variable Renamer (locals and builtins)
    source = _rename_variables(source, target="luau")

    # Step 2: String and Number encryption
    str_key = secrets.randbelow(256) + 1
    int_key = secrets.randbelow(256) + 1
    str_enc = StringEncrypter(source, str_key)
    source, str_decryptor = str_enc.apply()
    math_enc = MathEncrypter(source, int_key)
    source, int_decryptor = math_enc.apply()
    decryptor_code = str_decryptor + "\n" + int_decryptor

    # Step 3: Build global map for hiding function calls
    used: Set[str] = {v for k, v in _tokens(source) if k == "identifier"}
    parsed = _rename_locals(list(_tokens(source)), used)
    # Recompute locals after rename
    local_names = set()
    sig = [(i, k, v) for i, (k, v) in enumerate(parsed) if k not in ("whitespace", "comment", "long_comment")]
    depth = 0
    for i, (_, kind, val) in enumerate(sig):
        if val == "local" and depth == 0:
            nxt = i + 1
            if nxt < len(sig) and sig[nxt][2] == "function":
                nxt += 1
                if nxt < len(sig) and sig[nxt][1] == "identifier":
                    local_names.add(sig[nxt][2])
            else:
                expect = True
                while nxt < len(sig):
                    _, nk, nv = sig[nxt]
                    if nv in ("=", ";"):
                        break
                    if expect and nk == "identifier":
                        local_names.add(nv)
                        expect = False
                    elif nv == ",":
                        expect = True
                    nxt += 1
        if val in ("function", "do", "then", "repeat"):
            depth += 1
        elif val in ("end", "until"):
            depth = max(0, depth - 1)

    global_map = {}
    i = 0
    while i < len(parsed):
        kind, val = parsed[i]
        if kind == "identifier" and val not in local_names:
            j = i + 1
            while j < len(parsed) and parsed[j][0] in ("whitespace", "comment", "long_comment"):
                j += 1
            if j < len(parsed) and parsed[j][1] == "(":
                if i > 0 and parsed[i-1][1] not in (".", ":"):
                    global_map[val] = secrets.randbelow(0xFFFF) + 1
        i += 1

    # Generate unique name for lookup table
    lookup_name = _fresh(used)

    # Replace globals with lookup_name
    parsed = _replace_globals(parsed, local_names, global_map, lookup_name)

    # Process strings and numbers for the VM
    records = []
    out_tok = []
    for kind, val in parsed:
        if kind in ("comment", "long_comment"):
            continue
        if kind == "whitespace":
            continue
        if kind in ("string", "long_string"):
            rec = _encrypt(val)
            if rec is None:
                out_tok.append(val)
            else:
                records.append(rec)
                out_tok.append(f"__R{len(records)-1}__")
            continue
        if kind == "number":
            out_tok.append(_mask_num(val))
            continue
        out_tok.append(val)

    pool_ord = list(range(len(records)))
    secrets.SystemRandom().shuffle(pool_ord)
    o2n = {old: new for new, old in enumerate(pool_ord)}
    srecs = [records[o] for o in pool_ord]

    slots = list(range(16))
    secrets.SystemRandom().shuffle(slots)
    remap = slots[:2]
    stream = []
    for old_idx in range(len(records)):
        for _ in range(secrets.randbelow(3)):
            stream.append(_pack(1, secrets.randbelow(0xFFFF), remap))
        stream.append(_pack(0, o2n[old_idx], remap))
    for _ in range(secrets.randbelow(4)):
        stream.append(_pack(1, secrets.randbelow(0xFFFF), remap))

    stream_chk = 0
    for p in stream:
        stream_chk ^= p
    stream_chk &= 0xFFFFFFFF

    guard = 0
    a_rows, b_rows, c_rows, meta_rows = [], [], [], []
    for idx, rec in enumerate(srecs, 1):
        a_rows.append("{" + ",".join(map(str, rec["a"])) + "}")
        b_rows.append("{" + ",".join(map(str, rec["b"])) + "}")
        c_rows.append("{" + ",".join(map(str, rec["c"])) + "}")
        meta_rows.append("{" + ",".join(map(str, [rec["n"], rec["seed"], rec["add"], rec["step"], rec["blk"], rec["rot"], rec["rev"], rec["chk"]])) + "}")
        guard = (guard + idx * 31 + rec["seed"] * 19 + rec["add"] * 13 + rec["step"] * 7 + rec["blk"] * 23 + rec["rot"] * 5 + rec["rev"] * 3 + rec["chk"]) % 0x100000000

    final_guard = (guard ^ stream_chk) & 0xFFFFFFFF

    n_dec = _fresh(used)
    for i, t in enumerate(out_tok):
        if t.startswith("__R") and t.endswith("__"):
            oi = int(t[3:-2])
            out_tok[i] = f"{n_dec}({o2n[oi]})"

    body = _compact(out_tok).strip()

    # Control‑flow flattening + junk
    body = _flatten_control_flow(body, used)
    body = _insert_junk(body, used)

    # Runtime wrapper
    lookup_code = _generate_lookup_table(lookup_name, global_map)
    N = lambda: _fresh(used, confuse=True)
    n_bxor, n_bor, n_band, n_ls, n_rs = N(), N(), N(), N(), N()
    n_char, n_cat, n_type, n_floor, n_trap = N(), N(), N(), N(), N()
    n_tA, n_tB, n_tC, n_meta, n_cache = N(), N(), N(), N(), N()
    n_stream, n_hnd, n_disp, n_vms = N(), N(), N(), N()
    n_gv, n_cv = N(), N()
    n_i, n_j, n_r, n_out, n_v, n_pf = N(), N(), N(), N(), N(), N()
    n_op, n_opr, n_pc, n_ins, n_val, n_tmp = N(), N(), N(), N(), N(), N()
    dec = [N() for _ in range(5)]

    load_wire = _mba(remap[0])
    junk_wire = _mba(remap[1])
    jw_raw = remap[1]

    decoy_init = "\n".join(f"local {d}={_mba(secrets.randbelow(255))}" for d in dec)
    decoy_use = f"if false then {dec[0]}={dec[1]}+{dec[2]} {dec[3]}={dec[4]}-{dec[0]} end"

    rt = f"""\
local {n_bxor}=bit32.bxor
local {n_bor}=bit32.bor
local {n_band}=bit32.band
local {n_ls}=bit32.lshift
local {n_rs}=bit32.rshift
local {n_char}=string.char
local {n_cat}=table.concat
local {n_type}=type
local {n_floor}=math.floor
local {n_trap}=function()error("",0)end
{decoy_init}
{decoy_use}
if not({n_type}(bit32)=={n_type}({{}}))then {n_trap}()end
if not {_opaque()} then {n_trap}()end
local {n_tA}={{{",".join(a_rows)}}}
local {n_tB}={{{",".join(b_rows)}}}
local {n_tC}={{{",".join(c_rows)}}}
local {n_meta}={{{",".join(meta_rows)}}}
local {n_cache}={{}}
local {n_gv}=0
for {n_i}=1,#{n_meta} do
local {n_r}={n_meta}[{n_i}]
{n_gv}=({n_gv}+{n_i}*31+{n_r}[2]*19+{n_r}[3]*13+{n_r}[4]*7+{n_r}[5]*23+{n_r}[6]*5+{n_r}[7]*3+{n_r}[8])%4294967296
end
local {n_stream}={{{",".join(map(str, stream))}}}
local {n_cv}=0
for {n_i}=1,#{n_stream} do {n_cv}={n_bxor}({n_cv},{n_stream}[{n_i}])end
{n_cv}={n_band}({n_cv},4294967295)
if {n_bxor}({n_gv}%4294967296,{n_cv})~={_mba(final_guard)} then {n_trap}()end
if not {_opaque()} then {n_trap}()end
local {n_dec}=function({n_i})
if {n_cache}[{n_i}]~=nil then return {n_cache}[{n_i}]end
local {n_r}={n_meta}[{n_i}+1]
if {n_r}==nil then {n_trap}()end
local {n_out}={{}}
local {n_cv}=2166136261
local function {n_pf}(s,pos)
local sl=(pos-1)%3
local si={n_floor}((pos-1)/3)+1
if sl==0 then return {n_tA}[s][si]
elseif sl==1 then return {n_tB}[s][si]
else return {n_tC}[s][si]end
end
for {n_j}=1,{n_r}[1] do
local src={n_j}
if {n_r}[7]~=0 then src={n_r}[1]-{n_j}+1 end
local {n_v}={n_pf}({n_i}+1,src)
{n_v}={n_bxor}({n_v},{n_r}[5])
{n_v}={n_bor}({n_rs}({n_v},{n_r}[6]),{n_ls}({n_v},8-{n_r}[6]))
{n_v}={n_band}({n_v},255)
{n_v}={n_bxor}({n_v},({n_r}[2]+({n_j}-1)*{n_r}[4]+{n_r}[3])%256)
if {n_v}<0 then {n_v}={n_v}+256 end
{n_cv}={n_band}(({n_bxor}({n_cv},{n_v})*16777619),4294967295)
{n_out}[{n_j}]={n_char}({n_v})
end
if {n_cv}~={n_r}[8] then {n_trap}()end
local {n_val}={n_cat}({n_out})
{n_cache}[{n_i}]={n_val}
return {n_val}
end
local {n_hnd}={{}}
for {n_i}=0,15 do {n_hnd}[{n_i}]=function()return nil end end
{n_hnd}[{load_wire}]=function({n_opr})return {n_dec}({n_opr})end
{n_hnd}[{junk_wire}]=function()return nil end
if {n_hnd}[{_mba(jw_raw)}]()~=nil then {n_trap}()end
if not {_opaque()} then {n_trap}()end
local {n_vms}={{pc=1,last=nil}}
local {n_disp}=function()
local {n_pc}={n_vms}.pc
while {n_pc}<=#{n_stream} do
local {n_ins}={n_stream}[{n_pc}]
local {n_op}={n_ins}%16
local {n_opr}={n_floor}({n_ins}/64)%65536
local {n_tmp}={n_hnd}[{n_op}]
if {n_tmp} then
local {n_val}={n_tmp}({n_opr})
if {n_val}~=nil then {n_vms}.last={n_val} end
end
{n_pc}={n_pc}+1
end
{n_vms}.pc={n_pc}
end
{n_disp}()
{lookup_code}
"""

    header = [ATTRIBUTION]
    if decryptor_code:
        header.append(decryptor_code)
    header.append(rt)

    # Assemble final code, then minify it to one dense line
    final_code = "\n".join(header) + "\n" + body + "\n"
    return _minify(final_code)

# ── Helper functions ──────────────────────────────────────────────────────────
def _replace_globals(tokens: List[Tuple[str, str]], locals_set: Set[str],
                     global_map: Dict[str, int], lookup_name: str) -> List[Tuple[str, str]]:
    out = []
    i = 0
    while i < len(tokens):
        kind, val = tokens[i]
        if kind == "identifier" and val in global_map:
            j = i + 1
            while j < len(tokens) and tokens[j][0] in ("whitespace", "comment", "long_comment"):
                j += 1
            if j < len(tokens) and tokens[j][1] == "(":
                if i > 0 and tokens[i-1][1] not in (".", ":"):
                    out.append(("identifier", lookup_name))
                    out.append(("symbol", "["))
                    out.append(("number", str(global_map[val])))
                    out.append(("symbol", "]"))
                    i += 1
                    continue
        out.append((kind, val))
        i += 1
    return out

def _generate_lookup_table(lookup_name: str, global_map: Dict[str, int]) -> str:
    if not global_map:
        return ""
    parts = []
    for name, idx in global_map.items():
        parts.append(f"{lookup_name}[{idx}]={name}")
    return f"local {lookup_name} = {{}}\n" + "\n".join(parts) + "\n"

def _flatten_control_flow(code: str, used: Set[str]) -> str:
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

def _insert_junk(code: str, used: Set[str]) -> str:
    lines = code.splitlines()
    new_lines = []
    for line in lines:
        new_lines.append(line)
        if secrets.randbelow(100) < 30:
            dummy = _fresh(used)
            junk = f"{dummy}=({_mba(secrets.randbelow(0xFFFF))})"
            new_lines.append(junk)
        if secrets.randbelow(100) < 20:
            pred = _opaque()
            if secrets.randbelow(2) == 0:
                new_lines.append(f"if {pred} then end")
            else:
                new_lines.append(f"if not ({pred}) then end")
    return "\n".join(new_lines)

# ── Discord cog ────────────────────────────────────────────────────────────────
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
