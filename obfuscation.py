"""
Discord Cog: Multi‑layer Luau obfuscator with control‑flow flattening
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
# 2. ENCRYPTION LAYERS (multi-stage)
# -----------------------------------------------------------------------------

class CryptoParams:
    def __init__(self, payload_len: int):
        self.seed = random.randint(1, 2**31 - 1)
        self.layer1_key = random.randint(1, 255)
        self.layer2_rot = random.randint(1, 7)
        self.layer3_add = random.randint(1, 220)
        self.layer4_xor = [random.randint(1, 255) for _ in range(random.randint(3, 6))]
        self.layer5_perm = random.sample(range(payload_len), payload_len) if payload_len > 0 else []
        self.stream_a = random.randint(1000, 99999)
        self.stream_b = random.randint(1000, 99999)
        self.integrity_seed = random.randint(1, 0xFFFFFFFF)

def encrypt_payload(payload: bytes, params: CryptoParams) -> bytes:
    data = bytearray(payload)
    # Layer 1: XOR with single key
    for i in range(len(data)):
        data[i] ^= params.layer1_key
    # Layer 2: Rotate left
    rot = params.layer2_rot
    for i in range(len(data)):
        data[i] = ((data[i] << rot) | (data[i] >> (8 - rot))) & 0xFF
    # Layer 3: Add with position factor
    add = params.layer3_add
    for i in range(len(data)):
        data[i] = (data[i] + add + i) & 0xFF
    # Layer 4: XOR with key sequence
    keys = params.layer4_xor
    for i in range(len(data)):
        data[i] ^= keys[i % len(keys)]
    # Layer 5: Permutation (if payload is non-empty)
    if params.layer5_perm:
        data = bytearray(data[p] for p in params.layer5_perm)
    # Layer 6: Stream cipher (rolling XOR)
    state = (params.seed ^ params.stream_a) & 0xFFFFFFFF
    for i in range(len(data)):
        state = (state * 214013 + 2531011) & 0xFFFFFFFF
        data[i] ^= (state >> 16) & 0xFF
        data[i] ^= (i * params.stream_b + (params.seed & 0xFF)) & 0xFF
    return bytes(data)

# -----------------------------------------------------------------------------
# 3. DECODER GENERATOR (Luau-compatible)
# -----------------------------------------------------------------------------

class DecoderGenerator:
    @staticmethod
    def generate(params: CryptoParams, encrypted: bytes) -> Tuple[str, str]:
        used = set()
        # Random names for all locals
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

        # Data table (1‑based)
        data_str = "{" + ",".join(map(str, encrypted)) + "}"
        data_tbl_def = f"local {data_tbl}={{{data_str}}}"

        # Metadata: store all parameters in a table (1‑based)
        # We'll store as: {seed, layer1_key, layer2_rot, layer3_add, layer4_xor, layer5_perm, stream_a, stream_b, integrity_seed}
        # Since perm is a table, we'll store it as a table.
        perm_str = "{" + ",".join(map(str, params.layer5_perm)) + "}" if params.layer5_perm else "{}"
        xor_str = "{" + ",".join(map(str, params.layer4_xor)) + "}"
        meta_entry = "{" + ",".join([
            str(params.seed),
            str(params.layer1_key),
            str(params.layer2_rot),
            str(params.layer3_add),
            xor_str,
            perm_str,
            str(params.stream_a),
            str(params.stream_b),
            str(params.integrity_seed),
        ]) + "}"
        meta_str = "{" + meta_entry + "}"

        # We'll generate a decoder function that reverses the layers in reverse order.
        # To make it harder, we'll split the reversal into several helper functions with random names.
        # But for simplicity, we'll inline it (still hard to read).

        decoder = f"""
local {bx},{bo},{ba},{bl},{br}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {sch},{tcat}=string.char,table.concat
{data_tbl_def}
local {meta_tbl}={meta_str}
local {cache}={{}}
local function {dec}(idx)
if {cache}[idx]~=nil then return {cache}[idx] end
local m={meta_tbl}[idx+1]
local seed, layer1_key, layer2_rot, layer3_add, layer4_xor, layer5_perm, stream_a, stream_b, integrity_seed =
    m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9]
local src={data_tbl}
local t={{}} for i=1,#src do t[i]=src[i] end

-- Reverse stream (same as forward)
local state={bx}(seed,stream_a)
for i=1,#t do
    state=(state*214013+2531011)%4294967296
    t[i]={bx}(t[i],{ba}({br}(state,16),255))
    t[i]={bx}(t[i],{ba}((i-1)*stream_b+{ba}(seed,255),255))
end

-- Reverse permutation: compute inverse permutation
if #layer5_perm > 0 then
    local inv={{}} for i=1,#layer5_perm do inv[layer5_perm[i]+1]=i end
    local out={{}} for i=1,#t do out[i]=t[inv[i]] end
    t=out
end

-- Reverse layer 4: XOR with key sequence (self-inverse)
for i=1,#t do
    t[i]={bx}(t[i],layer4_xor[((i-1)%#layer4_xor)+1])
end

-- Reverse layer 3: subtract with position factor
for i=1,#t do
    t[i]=(t[i]-layer3_add-(i-1))%256
end

-- Reverse layer 2: rotate right
for i=1,#t do
    t[i]={bo}({br}(t[i],layer2_rot),{bl}(t[i],8-layer2_rot))
    t[i]={ba}(t[i],255)
end

-- Reverse layer 1: XOR with single key (self-inverse)
for i=1,#t do
    t[i]={bx}(t[i],layer1_key)
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
# 4. CONTROL-FLOW FLATTENER (adds state-machine wrapper)
# -----------------------------------------------------------------------------

def flatten_control_flow(code: str) -> str:
    """Wrap code in a while-state machine (simple flattening)."""
    state_var = "state_" + ''.join(random.choices(string.ascii_letters, k=8))
    lines = code.splitlines()
    flat = [
        f"local {state_var}=0",
        "while true do",
        f"if {state_var}==0 then"
    ]
    for line in lines:
        flat.append("    " + line)
    flat.append(f"    {state_var}=1")
    flat.append(f"elseif {state_var}==1 then")
    flat.append("    break")
    flat.append("end")
    flat.append("end")
    return "\n".join(flat)

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
        if 'loadstring' not in code and 'function' not in code:
            return False
        if code.count('(') != code.count(')'):
            return False
        return True

    def obfuscate(self, source: str) -> str:
        # 1. Minify
        minified = minify_lua(source)
        if not minified.strip():
            return HEADER + "\n" + minified

        # 2. Encrypt
        payload = minified.encode('utf-8')
        params = CryptoParams(len(payload))
        encrypted = encrypt_payload(payload, params)

        # 3. Generate decoder
        decoder_code, dec_name = DecoderGenerator.generate(params, encrypted)

        # 4. Add control-flow flattening to the decoder (optional but makes it harder)
        # We'll flatten the decoder function itself.
        # But the decoder is a function; we'll just wrap the whole script in a flattened block.
        # We'll flatten the entire main body.

        # 5. Build loader
        loader = f"""
local _payload = {dec_name}(0)
if not _payload then error("Corrupted payload", 0) end
local _fn, _err = loadstring(_payload)
if not _fn then error(_err, 0) end
_fn()
"""
        # 6. Combine
        full = f"""
{decoder_code}
{loader}
"""
        # 7. Wrap in IIFE and flatten control flow
        final = f"(function(){full} end)()"
        final = flatten_control_flow(final)
        final = re.sub(r'\s+', ' ', final).strip()

        # 8. Validate
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

    @app_commands.command(name="obf", description="Multi-layer Luau obfuscator with control-flow flattening")
    @app_commands.describe(file="Attach the .lua or .txt Luau source file")
    async def obf(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.lower().endswith((".lua", ".txt")):
            await interaction.response.send_message("Please upload a `.lua` or `.txt` file.", ephemeral=True)
            return

        if file.size > 750000:
            await interaction.response.send_message("File too large. Max 750KB.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            raw = await file.read()
            source = raw.decode("utf-8-sig")
            if not source.strip():
                await interaction.followup.send("Empty file.", ephemeral=True)
                return

            obf = Obfuscator()
            result = await asyncio.to_thread(obf.obfuscate, source)

            out = discord.File(io.BytesIO(result.encode()), filename=Path(file.filename).stem + ".obfuscated.lua")
            await interaction.followup.send(content="✅ Obfuscation complete!", file=out)

        except Exception as e:
            await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)

# -----------------------------------------------------------------------------
# 7. SETUP
# -----------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
