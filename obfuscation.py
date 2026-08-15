"""
Discord Cog: Luau‑compatible multi‑stage obfuscator
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
from typing import List, Dict, Set, Tuple, Optional

import discord
from discord import app_commands
from discord.ext import commands

# -----------------------------------------------------------------------------
# 1. UTILITIES
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
}
HEADER = "--[[obfuscated with buterfuscate - https://discord.gg/tdzc8R9BG]]--\n"

def random_name(used: Set[str], length: int = 0) -> str:
    length = length or random.randint(8, 14)
    chars = string.ascii_letters + string.digits + "_"
    while True:
        name = random.choice(string.ascii_letters + "_") + ''.join(random.choices(chars, k=length-1))
        if name not in used and name not in RESERVED:
            used.add(name)
            return name

def minify_lua(code: str) -> str:
    code = re.sub(r'--\[\[.*?\]\]|--[^\n]*', '', code, flags=re.DOTALL)
    code = re.sub(r'\s+', ' ', code).strip()
    return code

# -----------------------------------------------------------------------------
# 2. PARAMETER GENERATION
# -----------------------------------------------------------------------------

class CryptoParams:
    def __init__(self, payload_len: int):
        self.seed = random.randint(1, 2**31 - 1)
        self.rounds = random.randint(5, 12)
        self.xor_keys = [random.randint(1, 255) for _ in range(self.rounds)]
        self.rot_l = [random.randint(1, 7) for _ in range(self.rounds)]
        self.rot_r = [random.randint(1, 7) for _ in range(self.rounds)]
        self.add_const = [random.randint(1, 220) for _ in range(self.rounds)]
        self.pos_mul = random.choice([11, 13, 17, 19, 23, 29, 31, 37])
        self.stream_a = random.randint(1000, 99999)
        self.stream_b = random.randint(1000, 99999)
        self.integrity_seed = random.randint(1, 0xFFFFFFFF)

# -----------------------------------------------------------------------------
# 3. ENCODER (Python)
# -----------------------------------------------------------------------------

class Encoder:
    @staticmethod
    def apply_round(data: bytearray, params: CryptoParams, r: int) -> bytearray:
        k = params.xor_keys[r]
        add = params.add_const[r]
        fac = (r + 1) % 7 + 1
        rot = params.rot_l[r]
        rot_r = params.rot_r[r]
        pm = params.pos_mul
        seed = params.seed

        for i in range(len(data)):
            data[i] ^= k
        for i in range(len(data)):
            data[i] = (data[i] + add + i * fac) & 0xFF
        for i in range(len(data)):
            data[i] = ((data[i] << rot) | (data[i] >> (8 - rot))) & 0xFF
        for i in range(len(data)):
            data[i] ^= (i * pm + (seed & 0xFF) + r * 13) & 0xFF
        for i in range(len(data)):
            data[i] = ((data[i] >> rot_r) | (data[i] << (8 - rot_r))) & 0xFF
        return data

    @staticmethod
    def apply_stream(data: bytearray, params: CryptoParams) -> bytearray:
        state = (params.seed ^ params.stream_a) & 0xFFFFFFFF
        for i in range(len(data)):
            state = (state * 214013 + 2531011) & 0xFFFFFFFF
            data[i] ^= (state >> 16) & 0xFF
            data[i] ^= (i * params.stream_b + (params.seed & 0xFF)) & 0xFF
        return data

    @staticmethod
    def encode(payload: bytes, params: CryptoParams) -> bytes:
        data = bytearray(payload)
        for r in range(params.rounds):
            data = Encoder.apply_round(data, params, r)
        data = Encoder.apply_stream(data, params)
        return bytes(data)

# -----------------------------------------------------------------------------
# 4. DECODER GENERATOR (Lua) – uses bit32 and 1‑based indexing
# -----------------------------------------------------------------------------

class DecoderGenerator:
    @staticmethod
    def generate(params: CryptoParams, encrypted: bytes) -> Tuple[str, str]:
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

        # Metadata: store per-round values as tables (1‑based in Lua)
        xor_str = "{" + ",".join(map(str, params.xor_keys)) + "}"
        rot_l_str = "{" + ",".join(map(str, params.rot_l)) + "}"
        rot_r_str = "{" + ",".join(map(str, params.rot_r)) + "}"
        add_str = "{" + ",".join(map(str, params.add_const)) + "}"
        meta_entry = "{" + ",".join([
            str(params.seed),
            str(params.rounds),
            xor_str,
            rot_l_str,
            rot_r_str,
            add_str,
            str(params.pos_mul),
            str(params.stream_a),
            str(params.stream_b),
            str(params.integrity_seed),
        ]) + "}"
        meta_str = "{" + meta_entry + "}"

        decoder = f"""
local {bx},{bo},{ba},{bl},{br}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {sch},{tcat}=string.char,table.concat
{data_tbl_def}
local {meta_tbl}={meta_str}
local {cache}={{}}
local function {dec}(idx)
if {cache}[idx]~=nil then return {cache}[idx] end
local m={meta_tbl}[idx+1]
local seed, rounds, xor_keys, rot_l, rot_r, add_const, pos_mul, stream_a, stream_b, integrity_seed =
    m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10]
local src={data_tbl}
local t={{}} for i=1,#src do t[i]=src[i] end

-- Reverse stream
local state={bx}(seed,stream_a)
for i=1,#t do
    state=(state*214013+2531011)%4294967296
    t[i]={bx}(t[i],{ba}({br}(state,16),255))
    t[i]={bx}(t[i],{ba}((i-1)*stream_b+{ba}(seed,255),255))
end

-- Reverse rounds
for r=rounds,1,-1 do
    local k=xor_keys[r]
    local rot_lr=rot_l[r]
    local rot_rr=rot_r[r]
    local addc=add_const[r]
    local fac=(r%7)+1
    -- Inverse of rot_r (rotate left)
    for i=1,#t do
        t[i]={bo}({bl}(t[i],rot_rr),{br}(t[i],8-rot_rr))
        t[i]={ba}(t[i],255)
    end
    -- Inverse of position XOR (self-inverse)
    for i=1,#t do
        t[i]={bx}(t[i],{ba}((i-1)*pos_mul+{ba}(seed,255)+(r-1)*13,255))
    end
    -- Inverse of rot_l (rotate right)
    for i=1,#t do
        t[i]={bo}({br}(t[i],rot_lr),{bl}(t[i],8-rot_lr))
        t[i]={ba}(t[i],255)
    end
    -- Inverse of add
    for i=1,#t do
        t[i]=(t[i]-addc-((i-1)*fac))%256
    end
    -- Inverse of XOR
    for i=1,#t do
        t[i]={bx}(t[i],k)
    end
end

-- Integrity check: FNV-1a
local h=0x811C9DC5
for i=1,#t do
    h=({bx}(h,t[i])*0x01000193)%4294967296
end
if h~=integrity_seed then return nil end

-- Convert to string
local out={{}} for i=1,#t do out[i]={sch}(t[i]) end
local s={tcat}(out) {cache}[idx]=s return s
end
"""
        return decoder, dec

# -----------------------------------------------------------------------------
# 5. MAIN OBFUSCATOR
# -----------------------------------------------------------------------------

class Obfuscator:
    def __init__(self):
        self.used: Set[str] = set()
        self.random = random.Random()
        self.random.seed(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())

    def rn(self, length: int = 0) -> str:
        return random_name(self.used, length)

    def _validate(self, code: str) -> bool:
        if not code.strip():
            return False
        if 'loadstring' not in code or 'function' not in code:
            return False
        if code.count('(') != code.count(')'):
            return False
        return True

    def obfuscate(self, source: str) -> str:
        minified = minify_lua(source)
        if not minified.strip():
            return HEADER + "\n" + minified

        payload = minified.encode('utf-8')
        params = CryptoParams(len(payload))
        encrypted = Encoder.encode(payload, params)

        decoder_code, dec_name = DecoderGenerator.generate(params, encrypted)

        loader = f"""
local _payload = {dec_name}(0)
if not _payload then error("Corrupted payload", 0) end
local _fn, _err = loadstring(_payload)
if not _fn then error(_err, 0) end
_fn()
"""
        # Small junk for confusion
        junk = []
        for _ in range(random.randint(1, 3)):
            v = self.rn()
            junk.append(f"local {v}={random.randint(1,100)}")
        junk_code = "\n".join(junk)

        full = f"""
{decoder_code}
{junk_code}
{loader}
"""
        final = f"(function(){full} end)()"
        final = re.sub(r'\s+', ' ', final).strip()

        if not self._validate(final):
            raise RuntimeError("Obfuscation produced invalid Lua syntax")

        return HEADER + "\n" + final

# -----------------------------------------------------------------------------
# 6. DISCORD COG
# -----------------------------------------------------------------------------

MAX_SOURCE_BYTES = 750_000

def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.lua"

class Obfuscation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="obf", description="Obfuscate a Luau file with multi‑stage encryption")
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
# 7. SETUP
# -----------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
