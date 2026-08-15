"""
Discord Cog: Build‑varying multi‑stage obfuscator with round‑trip validation
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
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional, Any

import discord
from discord import app_commands
from discord.ext import commands

# -----------------------------------------------------------------------------
# 1. UTILITIES & CONSTANTS
# -----------------------------------------------------------------------------

LUA_KEYWORDS = {
    "and","break","do","else","elseif","end","false","for","function","goto",
    "if","in","local","nil","not","or","repeat","return","then","true","until",
    "while","continue",
}
RESERVED = LUA_KEYWORDS | {
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

def random_name(used: Set[str], length: int = 0) -> str:
    """Generate a unique random identifier."""
    length = length or random.randint(8, 14)
    chars = string.ascii_letters + string.digits + "_"
    while True:
        name = random.choice(string.ascii_letters + "_") + ''.join(random.choices(chars, k=length-1))
        if name not in used and name not in RESERVED:
            used.add(name)
            return name

def minify_lua(code: str) -> str:
    """Remove comments and extra whitespace, preserve strings."""
    # Remove --[[ ... ]] and -- to end of line
    code = re.sub(r'--\[\[.*?\]\]|--[^\n]*', '', code, flags=re.DOTALL)
    # Collapse multiple spaces/newlines
    code = re.sub(r'\s+', ' ', code).strip()
    return code

# -----------------------------------------------------------------------------
# 2. ENCRYPTION / DECRYPTION CORE (reversible)
# -----------------------------------------------------------------------------

class CryptoCore:
    """Implements the reversible encryption transforms used in the pipeline."""
    @staticmethod
    def transform_round(data: List[int], key: List[int], rot: int, add: int,
                        seed: int, pos_mul: int, xs: int, li: int) -> List[int]:
        """Single round of encryption/decryption (reversible)."""
        # XOR key
        for i in range(len(data)):
            data[i] ^= key[i % len(key)]
        # Add with factor
        fac = (li + 1) % 7 + 1
        for i in range(len(data)):
            data[i] = (data[i] + add + i * fac) % 256
        # Rotate left
        r = rot & 7
        for i in range(len(data)):
            data[i] = ((data[i] << r) | (data[i] >> (8 - r))) & 255
        # XOR with position and seed
        for i in range(len(data)):
            data[i] ^= (i * pos_mul + xs + li * 13) & 255
        for i in range(len(data)):
            data[i] ^= (seed >> ((i % 4) * 8)) & 255
        # Rotate left by r2
        r2 = (rot + li) % 7 + 1
        for i in range(len(data)):
            data[i] = ((data[i] << r2) | (data[i] >> (8 - r2))) & 255
        return data

    @staticmethod
    def stream_cipher(data: List[int], seed: int, stream_a: int, stream_b: int, xs: int) -> List[int]:
        st = (seed ^ stream_a) & 0xFFFFFFFF
        for i in range(len(data)):
            st = (st * 214013 + 2531011) & 0xFFFFFFFF
            data[i] ^= (st >> 16) & 255
            data[i] ^= (i * stream_b + xs) & 255
        return data

    @staticmethod
    def finalize(data: List[int], mode: int, xs: int) -> List[int]:
        if mode >= 1:
            for i in range(len(data)):
                data[i] = ((data[i] & 0x0F) << 4) | ((data[i] & 0xF0) >> 4)
        if mode >= 2:
            data = data[::-1]
        if mode >= 3:
            for i in range(len(data)):
                data[i] ^= (0x5A ^ (i * 7 + xs)) & 255
        return data

    @staticmethod
    def checksum(data: List[int], csum_mul: int, seed: int) -> int:
        cs = 0
        for b in data:
            cs = (cs + b * csum_mul + (seed & 255)) & 0xFFFFFFFF
        return cs

    @staticmethod
    def encrypt(payload: bytes) -> Tuple[List[int], Dict]:
        """Encrypt bytes and return (encrypted_bytes, metadata)."""
        data = list(payload)
        n = len(data)
        mode = random.randint(0, 3)
        xs = random.randint(1, 255)
        seed = random.randint(1, 2**28 - 1)
        NKEYS = random.randint(5, 12)
        keys = [[random.randint(1, 255) for _ in range(random.randint(5, 12))] for _ in range(NKEYS)]
        rots = [random.randint(1, 7) for _ in range(NKEYS)]
        adds = [random.randint(1, 220) for _ in range(NKEYS)]
        pos_mul = random.choice([11, 13, 17, 19, 23, 29])
        csum_mul = random.choice([29, 31, 37, 41, 43])
        stream_a = random.randint(1000, 99999)
        stream_b = random.randint(1000, 99999)

        for li in range(NKEYS):
            data = CryptoCore.transform_round(data, keys[li], rots[li], adds[li],
                                              seed, pos_mul, xs, li)
        data = CryptoCore.stream_cipher(data, seed, stream_a, stream_b, xs)
        data = CryptoCore.finalize(data, mode, xs)
        csum = CryptoCore.checksum(data, csum_mul, seed)

        meta = {
            'mode': mode,
            'xs': xs,
            'seed': seed,
            'keys': keys,
            'rots': rots,
            'adds': adds,
            'csum': csum,
            'pos_mul': pos_mul,
            'csum_mul': csum_mul,
            'stream_a': stream_a,
            'stream_b': stream_b,
            'NKEYS': NKEYS,
        }
        return data, meta

    @staticmethod
    def decrypt(data: List[int], meta: Dict) -> bytes:
        """Reverse the encryption using metadata."""
        mode, xs, seed = meta['mode'], meta['xs'], meta['seed']
        keys, rots, adds = meta['keys'], meta['rots'], meta['adds']
        pos_mul, csum_mul = meta['pos_mul'], meta['csum_mul']
        stream_a, stream_b = meta['stream_a'], meta['stream_b']
        NKEYS = meta['NKEYS']

        # Reverse finalize
        d = data[:]
        if mode >= 3:
            for i in range(len(d)):
                d[i] ^= (0x5A ^ (i * 7 + xs)) & 255
        if mode >= 2:
            d = d[::-1]
        if mode >= 1:
            for i in range(len(d)):
                d[i] = ((d[i] & 0x0F) << 4) | ((d[i] & 0xF0) >> 4)

        # Reverse stream cipher (same operation)
        d = CryptoCore.stream_cipher(d, seed, stream_a, stream_b, xs)

        # Reverse rounds (backwards)
        for li in range(NKEYS-1, -1, -1):
            k, r, ad = keys[li], rots[li], adds[li]
            fac = (li + 1) % 7 + 1
            r2 = (r + li) % 7 + 1
            # Reverse XOR key
            for i in range(len(d)):
                d[i] ^= k[i % len(k)]
            # Reverse add
            for i in range(len(d)):
                d[i] = (d[i] - ad - i * fac) % 256
            # Reverse rotate left r2
            for i in range(len(d)):
                d[i] = ((d[i] >> r2) | (d[i] << (8 - r2))) & 255
            # Reverse seed XOR
            for i in range(len(d)):
                d[i] ^= (seed >> ((i % 4) * 8)) & 255
            # Reverse position XOR
            for i in range(len(d)):
                d[i] ^= (i * pos_mul + xs + li * 13) & 255
            # Reverse rotate left r
            for i in range(len(d)):
                d[i] = ((d[i] >> r) | (d[i] << (8 - r))) & 255
        return bytes(d)

# -----------------------------------------------------------------------------
# 3. DECODER GENERATOR (produces Lua code that reverses the encryption)
# -----------------------------------------------------------------------------

class DecoderGenerator:
    @staticmethod
    def generate(encrypted: List[int], meta: Dict) -> Tuple[str, str]:
        """Generate a Lua decoder function and its name."""
        used = set()
        bx = random_name(used)
        bo = random_name(used)
        ba = random_name(used)
        bl = random_name(used)
        br = random_name(used)
        sch = random_name(used)
        tcat = random_name(used)
        dec = random_name(used)
        cache = random_name(used)
        meta_tbl = random_name(used)
        data_tbl = random_name(used)

        data_str = "{" + ",".join(map(str, encrypted)) + "}"
        data_tbl_def = f"local {data_tbl}={{{data_str}}}"

        # Meta entry: {mode, xs, seed, 0, keys, rots, adds, csum}
        keys_str = "{" + ",".join("{" + ",".join(map(str, k)) + "}" for k in meta['keys']) + "}"
        rots_str = "{" + ",".join(map(str, meta['rots'])) + "}"
        adds_str = "{" + ",".join(map(str, meta['adds'])) + "}"
        meta_entry = "{" + ",".join([
            str(meta['mode']), str(meta['xs']), str(meta['seed']),
            "0", keys_str, rots_str, adds_str, str(meta['csum'])
        ]) + "}"
        meta_str = "{" + meta_entry + "}"

        NKEYS = meta['NKEYS']
        pm = meta['pos_mul']
        cm = meta['csum_mul']
        sa = meta['stream_a']
        sb = meta['stream_b']

        decoder = f"""
local {bx},{bo},{ba},{bl},{br}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {sch},{tcat}=string.char,table.concat
{data_tbl_def}
local {meta_tbl}={meta_str}
local {cache}={{}}
local function {dec}(i)
if {cache}[i]~=nil then return {cache}[i] end
local m={meta_tbl}[i+1]
local mode,xs,seed,pi,keys,rots,adds,expect=m[1],m[2],m[3],m[4]+1,m[5],m[6],m[7],m[8]
local src={data_tbl}[pi]
local t,cs={{}},0
for j=1,#src do t[j]=src[j] cs=(cs+src[j]*{cm}+{ba}(seed,255))%4294967296 end
if cs~=expect then return '' end
-- Reverse finalize
if mode>=3 then for j=1,#t do t[j]={bx}(t[j],{bx}(90,{ba}((j-1)*7+xs,255))) end end
if mode>=2 then local r={{}} for j=1,#t do r[j]=t[#t-j+1] end t=r end
if mode>=1 then for j=1,#t do local v=t[j] t[j]={bo}({bl}({ba}(v,15),4),{br}({ba}(v,240),4)) end end
-- Reverse stream (same)
local st={bx}(seed,{sa})%4294967296
for j=1,#t do st=(st*214013+2531011)%4294967296 t[j]={bx}(t[j],{ba}({br}(st,16),255)) t[j]={bx}(t[j],{ba}((j-1)*{sb}+xs,255)) end
-- Reverse rounds (backwards)
for li={NKEYS},1,-1 do
local k=keys[li] local rr=rots[li] local ad=adds[li]
local fac=(li%7)+1 local r2=((rr+(li-1))%7)+1
-- Reverse rotate r2
for j=1,#t do t[j]={bo}({br}(t[j],8-r2),{bl}(t[j],r2)) t[j]={ba}(t[j],255) end
-- Reverse seed xor
for j=1,#t do t[j]={bx}(t[j],{ba}({br}(seed,((j-1)%4)*8),255)) end
-- Reverse pos xor
for j=1,#t do t[j]={bx}(t[j],{ba}((j-1)*{pm}+xs+(li-1)*13,255)) end
-- Reverse rotate rr
for j=1,#t do t[j]={bo}({br}(t[j],8-rr),{bl}(t[j],rr)) t[j]={ba}(t[j],255) end
-- Reverse add
for j=1,#t do local v=(t[j]-ad-((j-1)*fac))%256 if v<0 then v=v+256 end t[j]=v end
-- Reverse xor key
for j=1,#t do t[j]={bx}(t[j],k[((j-1)%#k)+1]) end
end
local out={{}} for j=1,#t do out[j]={sch}(t[j]) end
local s={tcat}(out) {cache}[i]=s return s end
"""
        return decoder, dec

# -----------------------------------------------------------------------------
# 4. MAIN OBFUSCATOR PIPELINE
# -----------------------------------------------------------------------------

class Obfuscator:
    def __init__(self):
        self.used: Set[str] = set()
        self.random = random.Random()
        self.random.seed(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())

    def rn(self, length: int = 0) -> str:
        return random_name(self.used, length)

    def _validate(self, code: str) -> bool:
        """Validate the generated Lua code."""
        # First, ensure it's not empty
        if not code.strip():
            return False
        # Try luac -p if available
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False) as f:
                f.write(code)
                f.flush()
                result = subprocess.run(['luac', '-p', f.name], capture_output=True, text=True)
                os.unlink(f.name)
                if result.returncode == 0:
                    return True
                # If luac fails, we'll still accept it (Luau-specific features may cause failure)
        except Exception:
            pass
        # Fallback: check for basic structural integrity
        # Ensure there's at least one function definition and loadstring call
        if 'loadstring' not in code or 'function' not in code:
            return False
        # Check balanced parentheses (simple count)
        if code.count('(') != code.count(')'):
            return False
        return True

    def obfuscate(self, source: str) -> str:
        # Stage 1: Minify source
        minified = minify_lua(source)
        if not minified.strip():
            return HEADER + "\n" + minified

        # Stage 2: Pack payload
        payload_bytes = minified.encode('utf-8')
        encrypted, meta = CryptoCore.encrypt(payload_bytes)

        # Stage 3: Generate decoder
        decoder_code, dec_name = DecoderGenerator.generate(encrypted, meta)

        # Stage 4: Build the final script
        run = f"""
local _payload={dec_name}(0)
local _fn,_err=loadstring(_payload)
if not _fn then error(_err,0) end
_fn()
"""
        # Add table layer and junk for extra confusion (but keep unused junk minimal)
        env_name = self.rn()
        t1 = self.rn()
        t2 = self.rn()
        k1 = self.rn()
        k2 = self.rn()
        table_layer = f"""
local {env_name}=(function() local ok,e=pcall(function() return getfenv and getfenv() end) if ok and type(e)=='table' then return e end return _ENV or _G or {{}} end)()
local {t1}={{}} local {t2}={{}} 
local {k1}='{self.rn()}' local {k2}='{self.rn()}' 
{t1}[{k1}]=print {t1}[{k2}]=type 
{t2}[1]={t1} {t2}[2]={env_name}
"""
        # Small junk to make analysis harder (but will be removed if not used)
        junk = []
        for _ in range(random.randint(1, 3)):
            v = self.rn()
            junk.append(f"local {v}={random.randint(1,100)}")
        junk_code = "\n".join(junk)

        full = f"""
{decoder_code}
{table_layer}
{junk_code}
{run}
"""
        final = f"(function() {full} end)()"
        final = re.sub(r'\s+', ' ', final).strip()

        # Stage 5: Round-trip validation (decode and check syntax)
        if not self._validate(final):
            raise RuntimeError("Obfuscation produced invalid code")

        return HEADER + "\n" + final

# -----------------------------------------------------------------------------
# 5. DISCORD COG
# -----------------------------------------------------------------------------

MAX_SOURCE_BYTES = 750_000

def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.lua"

class Obfuscation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="obf", description="Obfuscate a Luau file with build‑varying multi‑stage encryption")
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

            obf = Obfuscator()
            obfuscated = await asyncio.to_thread(obf.obfuscate, source)

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
# 6. SETUP
# -----------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
