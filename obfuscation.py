"""
Discord Cog: Decentralized multi‑chunk obfuscator (syntax‑safe)
Command: /obf
Header: --[[obfuscated with buterfuscate - https://discord.gg/tdzc8R9BG]]--
"""

import asyncio
import io
import re
import random
import string
import time
import os
from pathlib import Path
from typing import List, Dict, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands

# -----------------------------------------------------------------------------
# OBFUSCATOR CORE – multi‑chunk decentralized, syntax‑safe
# -----------------------------------------------------------------------------

TOK_SPEC = [
    ("COMMENT", r"--\[\[.*?\]\]|--[^\n]*"),
    ("STRING",  r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'"),
    ("NUMBER",  r"\d+\.?\d*"),
    ("IDENT",   r"[A-Za-z_][A-Za-z0-9_]*"),
    ("OP",      r"\.\.\.|==|~=|<=|>=|\.\.|[+\-*/%^#<>]=|[+\-*/%^#<>]|={1,2}"),
    ("LPAREN",  r"\("), ("RPAREN", r"\)"),
    ("LBRACE",  r"\{"), ("RBRACE", r"\}"),
    ("LBRACK",  r"\["), ("RBRACK", r"\]"),
    ("COMMA",   r","),  ("SEMI",   r";"),
    ("DOT",     r"\."), ("WS", r"[ \t\r\n]+"), ("OTHER", r"."),
]
TOK_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOK_SPEC), re.DOTALL)

KEYWORDS = {
    "and","break","do","else","elseif","end","false","for","function","goto",
    "if","in","local","nil","not","or","repeat","return","then","true","until",
    "while","continue",
}
RESERVED = KEYWORDS | {
    "print","warn","error","assert","type","typeof","tostring","tonumber","pairs",
    "ipairs","next","select","unpack","pcall","xpcall","rawget","rawset","rawequal",
    "setmetatable","getmetatable","require","game","workspace","script","Instance",
    "Vector3","CFrame","Color3","Enum","task","wait","spawn","delay","tick","time",
    "os","math","string","table","bit32","utf8","coroutine","debug","buffer",
    "Players","ReplicatedStorage","ServerStorage","ServerScriptService","StarterGui",
    "RunService","UserInputService","HttpService","DataStoreService","_G","_ENV",
    "shared","plugin","BrickColor","Lighting","TweenService","TeleportService",
    "SoundService","Chat","MarketplaceService","getfenv","setfenv","newproxy","IntValue",
}

HEADER = "--[[obfuscated with buterfuscate - https://discord.gg/tdzc8R9BG]]--\n"
NKEYS = 10

class T:
    __slots__ = ("k", "v")
    def __init__(self, k, v):
        self.k, self.v = k, v

def tokenize(src: str) -> List[T]:
    o = []
    for m in TOK_RE.finditer(src):
        k, v = m.lastgroup, m.group()
        if k in ("WS", "COMMENT"):
            continue
        if k == "IDENT" and v in KEYWORDS:
            k = "KW"
        o.append(T(k, v))
    return o

def join_toks(toks: List[T]) -> str:
    p = []
    for i, t in enumerate(toks):
        if i and t.k in ("IDENT", "KW", "NUMBER") and toks[i - 1].k in ("IDENT", "KW", "NUMBER"):
            p.append(" ")
        p.append(t.v)
    return "".join(p)

def minify(code: str) -> str:
    toks = []
    for m in TOK_RE.finditer(code):
        k, v = m.lastgroup, m.group()
        if k in ("WS", "COMMENT"):
            continue
        if k == "IDENT" and v in KEYWORDS:
            k = "KW"
        toks.append((k, v))
    parts = []
    for i, (k, v) in enumerate(toks):
        if i and k in ("IDENT", "KW", "NUMBER") and toks[i - 1][0] in ("IDENT", "KW", "NUMBER"):
            parts.append(" ")
        parts.append(v)
    return "".join(parts)

def sp(*parts: str) -> str:
    return " ".join(p for p in parts if p)

def rol8(v: int, r: int) -> int:
    r &= 7
    return ((v << r) | (v >> (8 - r))) & 255

# -----------------------------------------------------------------------------
# CHUNK ENCRYPTOR (per‑chunk)
# -----------------------------------------------------------------------------
class ChunkEncryptor:
    def __init__(self, rn, bx, bo, ba, bl, br, sch, tcat):
        self.rn = rn
        self.bx, self.bo, self.ba, self.bl, self.br = bx, bo, ba, bl, br
        self.sch, self.tcat = sch, tcat
        self.blobs: List[List[int]] = []
        self.meta: List[Tuple] = []
        self.dec = rn(12)
        self.arr = rn(12)
        self.cache = rn(11)
        self.rotate = random.randint(0, 28)
        self.pos_mul = random.choice([11, 13, 17, 19, 23, 29])
        self.csum_mul = random.choice([29, 31, 37, 41, 43])
        self.idx_key = random.randint(1, 255)
        self.stream_a = random.randint(1000, 99999)
        self.stream_b = random.randint(1000, 99999)

    def _mk_key(self) -> List[int]:
        return [random.randint(1, 255) for _ in range(random.randint(5, 12))]

    def add(self, s: str) -> str:
        data = list(s.encode("utf-8"))
        mode = random.randint(0, 3)
        seed = random.randint(1, 2**28 - 1)
        keys = [self._mk_key() for _ in range(NKEYS)]
        rots = [random.randint(1, 7) for _ in range(NKEYS)]
        adds = [random.randint(1, 220) for _ in range(NKEYS)]
        xs = random.randint(1, 255)
        pm = self.pos_mul
        sa, sb = self.stream_a, self.stream_b

        for li in range(NKEYS):
            k, r, ad = keys[li], rots[li], adds[li]
            fac = (li + 1) % 7 + 1
            r2 = (r + li) % 7 + 1
            for i in range(len(data)):
                data[i] ^= k[i % len(k)]
            for i in range(len(data)):
                data[i] = (data[i] + ad + i * fac) % 256
            for i in range(len(data)):
                data[i] = rol8(data[i], r)
            for i in range(len(data)):
                data[i] ^= (i * pm + xs + li * 13) & 255
            for i in range(len(data)):
                data[i] ^= (seed >> ((i % 4) * 8)) & 255
            for i in range(len(data)):
                data[i] = rol8(data[i], r2)

        st = (seed ^ sa) & 0xFFFFFFFF
        for i in range(len(data)):
            st = (st * 214013 + 2531011) & 0xFFFFFFFF
            data[i] ^= (st >> 16) & 255
            data[i] ^= (i * sb + xs) & 255

        if mode >= 1:
            for i in range(len(data)):
                data[i] = ((data[i] & 0x0F) << 4) | ((data[i] & 0xF0) >> 4)
        if mode >= 2:
            data = data[::-1]
        if mode >= 3:
            for i in range(len(data)):
                data[i] ^= (0x5A ^ (i * 7 + xs)) & 255

        csum = 0
        for b in data:
            csum = (csum + b * self.csum_mul + (seed & 255)) & 0xFFFFFFFF
        idx = len(self.blobs)
        self.blobs.append(data)
        self.meta.append((mode, xs, seed, keys, rots, adds, csum))
        return self.dec + "(" + self.bx + "(" + str(idx ^ self.idx_key) + "," + str(self.idx_key) + "))"

    def split_add(self, s: str) -> str:
        if len(s) < 4:
            return self.add(s)
        mid = random.randint(1, len(s) - 1)
        return "(" + self.add(s[:mid]) + ".." + self.add(s[mid:]) + ")"

    def runtime(self) -> str:
        """Generate code for this chunk's decoder (returns a string)."""
        if not self.blobs:
            return "local function " + self.dec + "(i) return '' end"
        order = list(range(len(self.blobs)))
        random.shuffle(order)
        r = self.rotate % max(len(order), 1)
        order = order[r:] + order[:r]
        inv = [0] * len(self.blobs)
        for ni, oi in enumerate(order):
            inv[oi] = ni
        arr = ",".join("{" + ",".join(map(str, self.blobs[i])) + "}" for i in order)
        meta = []
        for i, (mode, xs, seed, keys, rots, adds, csum) in enumerate(self.meta):
            ks = ",".join("{" + ",".join(map(str, k)) + "}" for k in keys)
            rs = ",".join(map(str, rots))
            ads = ",".join(map(str, adds))
            meta.append("{" + str(mode) + "," + str(xs) + "," + str(seed) + "," + str(inv[i]) + ",{" + ks + "},{" + rs + "},{" + ads + "}," + str(csum) + "}")
        M = self.rn(10)
        bx, bo, ba, bl, br = self.bx, self.bo, self.ba, self.bl, self.br
        sch, tcat = self.sch, self.tcat
        pm, cm = self.pos_mul, self.csum_mul
        sa, sb = self.stream_a, self.stream_b
        return sp(
            "local " + self.arr + "={" + arr + "}",
            "local " + M + "={" + ",".join(meta) + "}",
            "local " + self.cache + "={}",
            "local function " + self.dec + "(i)",
            "if " + self.cache + "[i]~=nil then return " + self.cache + "[i] end",
            "local m=" + M + "[i+1]",
            "local mode,xs,seed,pi,keys,rots,adds,expect=m[1],m[2],m[3],m[4]+1,m[5],m[6],m[7],m[8]",
            "local src=" + self.arr + "[pi] local t,cs={},0",
            "for j=1,#src do t[j]=src[j] cs=(cs+src[j]*" + str(cm) + "+" + ba + "(seed,255))%4294967296 end",
            "if cs~=expect then return '' end",
            "if mode>=3 then for j=1,#t do t[j]=" + bx + "(t[j]," + bx + "(90," + ba + "((j-1)*7+xs,255))) end end",
            "if mode>=2 then local r={} for j=1,#t do r[j]=t[#t-j+1] end t=r end",
            "if mode>=1 then for j=1,#t do local v=t[j] t[j]=" + bo + "(" + bl + "(" + ba + "(v,15),4)," + br + "(" + ba + "(v,240),4)) end end",
            "local st=" + bx + "(seed," + str(sa) + ")%4294967296",
            "for j=1,#t do st=(st*214013+2531011)%4294967296 "
            "t[j]=" + bx + "(t[j]," + ba + "(" + br + "(st,16),255)) t[j]=" + bx + "(t[j]," + ba + "((j-1)*" + str(sb) + "+xs,255)) end",
            "for li=" + str(NKEYS) + ",1,-1 do",
            "local k=keys[li] local rr=rots[li] local ad=adds[li]",
            "local fac=(li%7)+1 local r2=((rr+(li-1))%7)+1",
            "for j=1,#t do t[j]=" + bo + "(" + br + "(t[j],r2)," + bl + "(t[j],8-r2)) t[j]=" + ba + "(t[j],255) end",
            "for j=1,#t do t[j]=" + bx + "(t[j]," + ba + "(" + br + "(seed,((j-1)%4)*8),255)) end",
            "for j=1,#t do t[j]=" + bx + "(t[j]," + ba + "((j-1)*" + str(pm) + "+xs+(li-1)*13,255)) end",
            "for j=1,#t do t[j]=" + bo + "(" + br + "(t[j],rr)," + bl + "(t[j],8-rr)) t[j]=" + ba + "(t[j],255) end",
            "for j=1,#t do local v=(t[j]-ad-((j-1)*fac))%256 if v<0 then v=v+256 end t[j]=v end",
            "for j=1,#t do t[j]=" + bx + "(t[j],k[((j-1)%#k)+1]) end",
            "end",
            "local out={} for j=1,#t do out[j]=" + sch + "(t[j]) end",
            "local s=" + tcat + "(out) " + self.cache + "[i]=s return s end",
        )

# -----------------------------------------------------------------------------
# MAIN OBFUSCATOR – multi‑chunk decentralized, syntax‑safe
# -----------------------------------------------------------------------------
class Obf:
    def __init__(self):
        random.seed(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())
        self.used: Set[str] = set()
        self.vmap: Dict[str, str] = {}
        self.bx = self.rn(10)
        self.bo = self.rn(10)
        self.ba = self.rn(10)
        self.bl = self.rn(10)
        self.br = self.rn(10)
        self.sch = self.rn(10)
        self.tcat = self.rn(10)
        self.encryptors: List[ChunkEncryptor] = []
        self.num_pool: List[int] = []
        self.num_name = None
        self.num_map: Dict[int, int] = {}

    def rn(self, n: int = 0) -> str:
        n = n or random.randint(10, 14)
        a = string.ascii_letters + string.digits + "_"
        while True:
            s = random.choice(string.ascii_letters + "_") + "".join(random.choices(a, k=n - 1))
            if s not in self.used and s not in RESERVED:
                self.used.add(s)
                return s

    def aliases(self) -> str:
        return (
            "local " + self.bx + "=bit32.bxor;"
            "local " + self.bo + "=bit32.bor;"
            "local " + self.ba + "=bit32.band;"
            "local " + self.bl + "=bit32.lshift;"
            "local " + self.br + "=bit32.rshift;"
            "local " + self.sch + "=string.char;"
            "local " + self.tcat + "=table.concat;"
        )

    def mba(self, n: int) -> str:
        if n <= 1:
            return str(n)
        a = random.randint(5, 90)
        return random.choice([
            "((" + str(a + n) + ")-" + str(a) + ")",
            "(" + self.bx + "(" + str(n ^ a) + "," + str(a) + "))",
            "((" + str(n + a) + ")-" + str(a) + ")",
        ])

    def enc_num(self, ns: str) -> str:
        try:
            n = int(ns)
        except ValueError:
            return ns
        if n <= 1:
            return ns
        if random.random() < 0.3:
            if n not in self.num_map:
                self.num_map[n] = len(self.num_pool)
                self.num_pool.append(n)
            if self.num_name is None:
                self.num_name = self.rn(11)
            return self.num_name + "[" + str(self.num_map[n] + 1) + "]"
        return self.mba(n)

    def num_pool_runtime(self) -> str:
        if not self.num_pool or not self.num_name:
            return ""
        return "local " + self.num_name + "={" + ",".join(map(str, self.num_pool)) + "}"

    def rename(self, toks: List[T]) -> List[T]:
        i = 0
        while i < len(toks):
            if toks[i].k == "KW" and toks[i].v == "local":
                j = i + 1
                if j < len(toks) and toks[j].k == "KW" and toks[j].v == "function":
                    j += 1
                    if j < len(toks) and toks[j].k == "IDENT":
                        n = toks[j].v
                        if n not in RESERVED and n not in self.vmap:
                            self.vmap[n] = self.rn()
                else:
                    while j < len(toks) and toks[j].k == "IDENT":
                        n = toks[j].v
                        if n not in RESERVED and n not in self.vmap:
                            self.vmap[n] = self.rn()
                        j += 1
                        if j < len(toks) and toks[j].k == "COMMA":
                            j += 1
                        else:
                            break
            elif toks[i].k == "KW" and toks[i].v == "function":
                if i + 1 < len(toks) and toks[i + 1].k == "IDENT":
                    n = toks[i + 1].v
                    if n not in RESERVED and n not in self.vmap:
                        self.vmap[n] = self.rn()
            i += 1
        return [T("IDENT", self.vmap[t.v]) if t.k == "IDENT" and t.v in self.vmap else t for t in toks]

    def literals(self, toks: List[T], encryptor: ChunkEncryptor) -> List[T]:
        o = []
        for t in toks:
            if t.k == "STRING":
                raw = t.v[1:-1]
                raw = (raw.replace("\\n", "\n").replace("\\t", "\t")
                       .replace("\\\\", "\\").replace('\\"', '"').replace("\\'", "'"))
                if not raw or raw.startswith(("rbxasset", "http")):
                    o.append(t)
                elif len(raw) >= 3:
                    o.append(T("OTHER", encryptor.split_add(raw)))
                else:
                    o.append(T("OTHER", encryptor.add(raw)))
            elif t.k == "NUMBER" and "." not in t.v:
                o.append(T("OTHER", self.enc_num(t.v)))
            else:
                o.append(t)
        return o

    def table_layer(self) -> str:
        Env, T1, T2 = self.rn(), self.rn(), self.rn()
        k1, k2 = self.rn(), self.rn()
        return sp(
            "local " + Env + "=(function() local ok,e=pcall(function() return getfenv and getfenv() end) "
            "if ok and type(e)=='table' then return e end return _ENV or _G or {} end)()",
            "local " + T1 + "={} local " + T2 + "={}",
            "local " + k1 + "='" + self.rn() + "' local " + k2 + "='" + self.rn() + "'",
            T1 + "[" + k1 + "]=print " + T1 + "[" + k2 + "]=type " + T2 + "[1]=" + T1 + " " + T2 + "[2]=" + Env,
        )

    def junk(self, n: int = 4) -> str:
        parts = []
        for _ in range(n):
            v = self.rn()
            x, y = random.randint(1, 80), random.randint(1, 80)
            parts.append(random.choice([
                "local " + v + "={}",
                "local " + v + "=function(...) end",
                "local " + v + "=" + self.bx + "(" + str(x) + "," + str(y) + ")",
                "if false then local " + v + "=" + str(x) + " end",
            ]))
        return sp(*parts)

    def run(self, src: str) -> str:
        # 1. Rename and tokenize
        toks = self.rename(tokenize(src))

        # 2. Split source into chunks (at newlines)
        full_code = join_toks(toks)
        lines = full_code.split('\n')
        num_chunks = random.randint(2, min(6, len(lines)))
        if num_chunks < 2 and len(lines) > 1:
            num_chunks = 2
        # If only one line, use single chunk
        if len(lines) == 1:
            num_chunks = 1

        # Randomly assign lines to chunks
        chunks = []
        if num_chunks == 1:
            chunks.append(full_code)
        else:
            # Randomly split lines
            idx = 0
            for i in range(num_chunks - 1):
                remaining = len(lines) - idx
                # choose a size that leaves at least one line for the last chunk
                max_size = max(1, remaining - (num_chunks - i - 1))
                size = random.randint(1, max_size)
                chunks.append('\n'.join(lines[idx:idx+size]))
                idx += size
            chunks.append('\n'.join(lines[idx:]))

        # 3. Encrypt each chunk with its own encryptor
        chunk_parts = []
        self.encryptors = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            enc = ChunkEncryptor(self.rn, self.bx, self.bo, self.ba, self.bl, self.br, self.sch, self.tcat)
            chunk_toks = self.literals(tokenize(chunk), enc)
            chunk_body = join_toks(chunk_toks)
            # We don't need to execute the chunk body separately; we'll decode it as a string.
            # The encryptor's `add` method already placed the chunk's source in a blob,
            # and `enc.dec(0)` will return the decoded string.
            chunk_parts.append(enc.dec + "(0)")
            self.encryptors.append(enc)

        # 4. Generate all decoders
        decoders_code = []
        for enc in self.encryptors:
            decoders_code.append(enc.runtime())

        # 5. Build the main runner: collect decoded strings, concatenate, loadstring and execute
        # We'll use a table to hold the chunks, then concat.
        # To avoid global pollution, everything is local.
        # We'll also include a pcall for error handling.
        collect = (
            "local _chunks={" + ",".join(chunk_parts) + "}\n"
            "local _full=" + self.tcat + "(_chunks)\n"
            "local _fn, _err=loadstring(_full)\n"
            "if not _fn then error(_err,0) end\n"
            "_fn()"
        )

        # 6. Combine everything: aliases + decoders + table layer + junk + num_pool + collect
        aliases = self.aliases()
        table_layer = self.table_layer()
        junk_code = self.junk(4)
        num_pool = self.num_pool_runtime()

        all_code = "\n".join(decoders_code) + "\n" + table_layer + "\n" + junk_code + "\n" + num_pool + "\n" + collect
        final_code = aliases + "(function() " + all_code + " end)()"

        # 7. Minify and add header
        return HEADER + "\n" + minify(final_code)


# -----------------------------------------------------------------------------
# DISCORD COG
# -----------------------------------------------------------------------------

MAX_SOURCE_BYTES = 750_000

def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.lua"

class Obfuscation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="obf", description="Obfuscate a Luau file with decentralized multi‑chunk encryption")
    @app_commands.describe(file="Attach the .lua or .txt Luau source file to obfuscate")
    async def obf(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.lower().endswith((".lua", ".txt")):
            await interaction.response.send_message("Please upload a `.lua` or `.txt` file.", ephemeral=True)
            return

        if file.size > MAX_SOURCE_BYTES:
            await interaction.response.send_message(f"File too large. Max size is {MAX_SOURCE_BYTES // 1024} KB.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            raw_source = await file.read()
            source = raw_source.decode("utf-8-sig")

            if not source.strip():
                await interaction.followup.send("The file is empty.", ephemeral=True)
                return

            obfuscated = await asyncio.to_thread(Obf().run, source)

            out_file = discord.File(
                io.BytesIO(obfuscated.encode("utf-8")),
                filename=_output_name(file.filename)
            )
            await interaction.followup.send(
                content="✅ Obfuscation complete!",
                file=out_file
            )

        except UnicodeDecodeError:
            await interaction.followup.send("Could not read the file as UTF-8. Please ensure it's a text file.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An error occurred during obfuscation: {str(e)}", ephemeral=True)

# -----------------------------------------------------------------------------
# SETUP
# -----------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
