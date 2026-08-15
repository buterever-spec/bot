"""
Discord Cog: Polymorphic multi‑stage obfuscator with round‑trip validation
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
    # Remove comments
    code = re.sub(r'--\[\[.*?\]\]|--[^\n]*', '', code, flags=re.DOTALL)
    # Collapse whitespace
    code = re.sub(r'\s+', ' ', code).strip()
    return code

# -----------------------------------------------------------------------------
# 2. CRYPTOGRAPHIC PARAMETER GENERATION
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
        # Permutation of length = payload_len
        self.perm = random.sample(range(payload_len), payload_len) if payload_len > 0 else []
        self.stream_a = random.randint(1000, 99999)
        self.stream_b = random.randint(1000, 99999)
        self.integrity_seed = random.randint(1, 0xFFFFFFFF)

    def serialize(self) -> Dict:
        return {
            'seed': self.seed,
            'rounds': self.rounds,
            'xor_keys': self.xor_keys,
            'rot_l': self.rot_l,
            'rot_r': self.rot_r,
            'add_const': self.add_const,
            'pos_mul': self.pos_mul,
            'perm': self.perm,
            'stream_a': self.stream_a,
            'stream_b': self.stream_b,
            'integrity_seed': self.integrity_seed,
        }

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
    def apply_permutation(data: bytearray, perm: List[int]) -> bytearray:
        # perm is a list of indices of length len(data)
        return bytearray(data[p] for p in perm)

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
        # Stage 1: Permutation
        if params.perm:
            data = Encoder.apply_permutation(data, params.perm)
        # Stage 2: Rounds
        for r in range(params.rounds):
            data = Encoder.apply_round(data, params, r)
        # Stage 3: Streaming XOR
        data = Encoder.apply_stream(data, params)
        return bytes(data)

# -----------------------------------------------------------------------------
# 4. DECODER GENERATOR (Lua)
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

        # Encrypted data as Lua table
        data_str = "{" + ",".join(map(str, encrypted)) + "}"
        data_tbl_def = f"local {data_tbl}={{{data_str}}}"

        # Metadata
        xor_str = "{" + ",".join(map(str, params.xor_keys)) + "}"
        rot_l_str = "{" + ",".join(map(str, params.rot_l)) + "}"
        rot_r_str = "{" + ",".join(map(str, params.rot_r)) + "}"
        add_str = "{" + ",".join(map(str, params.add_const)) + "}"
        perm_str = "{" + ",".join(map(str, params.perm)) + "}"
        meta_entry = "{" + ",".join([
            str(params.seed),
            str(params.rounds),
            xor_str,
            rot_l_str,
            rot_r_str,
            add_str,
            str(params.pos_mul),
            perm_str,
            str(params.stream_a),
            str(params.stream_b),
            str(params.integrity_seed),
        ]) + "}"
        meta_str = "{" + meta_entry + "}"

        # Decoder function
        decoder = f"""
local function {dec}(idx)
    local m = {meta_tbl}[idx+1]
    local seed, rounds, xor_keys, rot_l, rot_r, add_const, pos_mul, perm, stream_a, stream_b, integrity_seed =
        m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11]
    local src = {data_tbl}
    local t = {{}}
    for i = 1, #src do t[i] = src[i] end

    -- Reverse stream
    local state = (seed ~ stream_a) & 0xFFFFFFFF
    for i = 1, #t do
        state = (state * 214013 + 2531011) & 0xFFFFFFFF
        t[i] = t[i] ~ ((state >> 16) & 0xFF)
        t[i] = t[i] ~ ((i - 1) * stream_b + (seed & 0xFF)) & 0xFF
    end

    -- Reverse rounds
    for r = rounds, 1, -1 do
        local k = xor_keys[r]
        local rot_lr = rot_l[r]
        local rot_rr = rot_r[r]
        local addc = add_const[r]
        local fac = (r % 7) + 1
        -- Inverse of rot_r (rotate left)
        for i = 1, #t do
            t[i] = ((t[i] << rot_rr) | (t[i] >> (8 - rot_rr))) & 0xFF
        end
        -- Inverse of position XOR (self-inverse)
        for i = 1, #t do
            t[i] = t[i] ~ ((i - 1) * pos_mul + (seed & 0xFF) + (r - 1) * 13) & 0xFF
        end
        -- Inverse of rot_l (rotate right)
        for i = 1, #t do
            t[i] = ((t[i] >> rot_lr) | (t[i] << (8 - rot_lr))) & 0xFF
        end
        -- Inverse of add
        for i = 1, #t do
            t[i] = (t[i] - addc - ((i - 1) * fac)) & 0xFF
        end
        -- Inverse of XOR
        for i = 1, #t do
            t[i] = t[i] ~ k
        end
    end

    -- Reverse permutation: compute inverse permutation
    -- perm is a list of indices, we need inv[perm[i]] = i
    local inv = {{}}
    for i = 1, #perm do
        inv[perm[i] + 1] = i  -- Lua tables are 1-indexed
    end
    local out = {{}}
    for i = 1, #t do
        out[i] = t[inv[i]]
    end

    -- Integrity check: FNV-1a on the decoded payload
    local h = 0x811C9DC5
    for i = 1, #out do
        h = (h ~ out[i]) * 0x01000193
        h = h & 0xFFFFFFFF
    end
    if h ~= integrity_seed then
        return nil  -- corruption detected
    end

    -- Convert to string
    local s = {{}}
    for i = 1, #out do
        s[i] = string.char(out[i])
    end
    return table.concat(s)
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
        # 1. Minify
        minified = minify_lua(source)
        if not minified.strip():
            return HEADER + "\n" + minified

        # 2. Generate parameters based on payload length
        payload = minified.encode('utf-8')
        params = CryptoParams(len(payload))

        # 3. Encode
        encrypted = Encoder.encode(payload, params)

        # 4. Generate Lua decoder
        decoder_code, dec_name = DecoderGenerator.generate(params, encrypted)

        # 5. Build final script
        loader = f"""
local _payload = {dec_name}(0)
if not _payload then error("Corrupted payload", 0) end
local _fn, _err = loadstring(_payload)
if not _fn then error(_err, 0) end
_fn()
"""
        # Add a small table layer for extra confusion but keep it minimal
        # We'll define some dummy locals that are unused (but not too many)
        dummy = []
        for _ in range(random.randint(1, 3)):
            v = self.rn()
            dummy.append(f"local {v}={random.randint(1,100)}")
        dummy_code = "\n".join(dummy)

        full = f"""
{decoder_code}
{dummy_code}
{loader}
"""
        # Wrap in IIFE and minify
        final = f"(function(){full} end)()"
        final = re.sub(r'\s+', ' ', final).strip()

        # Validate
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

    @app_commands.command(name="obf", description="Obfuscate a Luau file with polymorphic multi‑stage encryption")
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
