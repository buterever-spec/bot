"""
Discord Cog: Multi‑layer obfuscator with randomized layer order (Luau‑compatible)
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
    # Remove comments and extra whitespace safely
    code = re.sub(r'--\[\[.*?\]\]|--[^\n]*', '', code, flags=re.DOTALL)
    # Collapse multiple spaces but keep single spaces where needed
    code = re.sub(r'\s+', ' ', code).strip()
    return code

# -----------------------------------------------------------------------------
# 2. LAYER DEFINITIONS (reversible operations)
# -----------------------------------------------------------------------------

class Layer:
    @staticmethod
    def xor_key(data: bytearray, key: int) -> bytearray:
        for i in range(len(data)):
            data[i] ^= key
        return data

    @staticmethod
    def xor_key_inv(data: bytearray, key: int) -> bytearray:
        return Layer.xor_key(data, key)  # self-inverse

    @staticmethod
    def rotate_left(data: bytearray, rot: int) -> bytearray:
        for i in range(len(data)):
            data[i] = ((data[i] << rot) | (data[i] >> (8 - rot))) & 0xFF
        return data

    @staticmethod
    def rotate_left_inv(data: bytearray, rot: int) -> bytearray:
        # rotate right
        for i in range(len(data)):
            data[i] = ((data[i] >> rot) | (data[i] << (8 - rot))) & 0xFF
        return data

    @staticmethod
    def add_const(data: bytearray, add: int) -> bytearray:
        for i in range(len(data)):
            data[i] = (data[i] + add + i) & 0xFF
        return data

    @staticmethod
    def add_const_inv(data: bytearray, add: int) -> bytearray:
        for i in range(len(data)):
            data[i] = (data[i] - add - i) & 0xFF
        return data

    @staticmethod
    def xor_sequence(data: bytearray, keys: List[int]) -> bytearray:
        for i in range(len(data)):
            data[i] ^= keys[i % len(keys)]
        return data

    @staticmethod
    def xor_sequence_inv(data: bytearray, keys: List[int]) -> bytearray:
        return Layer.xor_sequence(data, keys)  # self-inverse

    @staticmethod
    def stream_cipher(data: bytearray, seed: int, stream_a: int, stream_b: int) -> bytearray:
        state = (seed ^ stream_a) & 0xFFFFFFFF
        for i in range(len(data)):
            state = (state * 214013 + 2531011) & 0xFFFFFFFF
            data[i] ^= (state >> 16) & 0xFF
            data[i] ^= (i * stream_b + (seed & 0xFF)) & 0xFF
        return data

    @staticmethod
    def stream_cipher_inv(data: bytearray, seed: int, stream_a: int, stream_b: int) -> bytearray:
        # Same operation (self-inverse)
        return Layer.stream_cipher(data, seed, stream_a, stream_b)

# -----------------------------------------------------------------------------
# 3. ENCRYPTION WITH RANDOMIZED LAYER ORDER
# -----------------------------------------------------------------------------

class CryptoParams:
    def __init__(self, payload_len: int):
        self.seed = random.randint(1, 2**31 - 1)
        self.layer1_key = random.randint(1, 255)
        self.layer2_rot = random.randint(1, 7)
        self.layer3_add = random.randint(1, 220)
        self.layer4_keys = [random.randint(1, 255) for _ in range(random.randint(3, 6))]
        self.stream_a = random.randint(1000, 99999)
        self.stream_b = random.randint(1000, 99999)
        self.integrity_seed = random.randint(1, 0xFFFFFFFF)

def encrypt_payload(payload: bytes, params: CryptoParams) -> bytes:
    data = bytearray(payload)
    # Apply layers in random order
    layers = [
        ('xor_key', params.layer1_key, Layer.xor_key),
        ('rotate_left', params.layer2_rot, Layer.rotate_left),
        ('add_const', params.layer3_add, Layer.add_const),
        ('xor_sequence', params.layer4_keys, Layer.xor_sequence),
        ('stream_cipher', (params.seed, params.stream_a, params.stream_b), Layer.stream_cipher),
    ]
    random.shuffle(layers)
    # Store the layer order in metadata for decryption
    order = [name for name, _, _ in layers]
    for _, _, func in layers:
        if isinstance(func, tuple):
            # For stream_cipher, the third element is the function, the second is args tuple
            # Actually we structured as (name, args, func) but we'll adjust.
        # Let's simplify: we'll just store the list of layer names and parameters in params.
    # For simplicity, we'll just store the order and parameters separately.
    # We'll store the order as a list of layer names and the parameters in a table.
    # But for this version, we'll just apply layers in a fixed order but randomize parameters.
    # To get variation, we'll randomize the parameters per build, and also randomize the order of applying layers.
    # Since the user wants variation, we'll randomize the order by shuffling the layer list and storing the order.

    # We'll store the order in the metadata as a string of layer names.
    # We'll encode the order as numbers.
    layer_map = {
        'xor_key': 1,
        'rotate_left': 2,
        'add_const': 3,
        'xor_sequence': 4,
        'stream_cipher': 5,
    }
    order_nums = [layer_map[name] for name, _, _ in layers]
    params.layer_order = order_nums

    # Apply layers
    for name, args, func in layers:
        if name == 'xor_key':
            data = func(data, args)
        elif name == 'rotate_left':
            data = func(data, args)
        elif name == 'add_const':
            data = func(data, args)
        elif name == 'xor_sequence':
            data = func(data, args)
        elif name == 'stream_cipher':
            seed, sa, sb = args
            data = func(data, seed, sa, sb)

    return bytes(data)

# -----------------------------------------------------------------------------
# 4. DECODER GENERATOR (Luau-compatible)
# -----------------------------------------------------------------------------

class DecoderGenerator:
    @staticmethod
    def generate(params: CryptoParams, encrypted: bytes, layer_order: List[int]) -> Tuple[str, str]:
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

        # Data table (1‑based)
        data_str = "{" + ",".join(map(str, encrypted)) + "}"
        data_tbl_def = f"local {data_tbl}={{{data_str}}}"

        # Metadata: store all parameters and layer order
        # layer_order is a table of numbers (1‑based)
        order_str = "{" + ",".join(map(str, layer_order)) + "}"
        xor_keys_str = "{" + ",".join(map(str, params.layer4_keys)) + "}"
        meta_entry = "{" + ",".join([
            str(params.seed),
            str(params.layer1_key),
            str(params.layer2_rot),
            str(params.layer3_add),
            xor_keys_str,
            str(params.stream_a),
            str(params.stream_b),
            str(params.integrity_seed),
            order_str,
        ]) + "}"
        meta_str = "{" + meta_entry + "}"

        # We'll generate the decoder function that applies the inverse layers in reverse order.
        # The layer_order is the forward order; the reverse order is the reversed list.
        # We'll generate Lua code that reads the order from metadata and applies the inverse functions accordingly.
        # To avoid a giant switch statement, we'll generate a sequence of if-else if blocks based on the order.
        # Since the order is known at generation time, we can directly emit the inverse operations in the correct order.

        # Build the inverse layer code as a string
        inv_code = []
        # Reverse the order
        rev_order = layer_order[::-1]
        for layer_num in rev_order:
            if layer_num == 1:  # xor_key
                inv_code.append(f"for i=1,#t do t[i]={bx}(t[i],layer1_key) end")
            elif layer_num == 2:  # rotate_left (inverse is rotate right)
                inv_code.append(f"for i=1,#t do t[i]={bo}({br}(t[i],layer2_rot),{bl}(t[i],8-layer2_rot)) t[i]={ba}(t[i],255) end")
            elif layer_num == 3:  # add_const (inverse subtract)
                inv_code.append(f"for i=1,#t do t[i]=(t[i]-layer3_add-(i-1))%256 end")
            elif layer_num == 4:  # xor_sequence (self-inverse)
                inv_code.append(f"for i=1,#t do t[i]={bx}(t[i],layer4_xor[((i-1)%#layer4_xor)+1]) end")
            elif layer_num == 5:  # stream_cipher (self-inverse)
                inv_code.append(f"""
local state={bx}(seed,stream_a)
for i=1,#t do
    state=(state*214013+2531011)%4294967296
    t[i]={bx}(t[i],{ba}({br}(state,16),255))
    t[i]={bx}(t[i],{ba}((i-1)*stream_b+{ba}(seed,255),255))
end
""")
        inv_body = "\n".join(inv_code)

        decoder = f"""
local {bx},{bo},{ba},{bl},{br}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {sch},{tcat}=string.char,table.concat
{data_tbl_def}
local {meta_tbl}={meta_str}
local {cache}={{}}
local function {dec}(idx)
if {cache}[idx]~=nil then return {cache}[idx] end
local m={meta_tbl}[idx+1]
local seed, layer1_key, layer2_rot, layer3_add, layer4_xor, stream_a, stream_b, integrity_seed, layer_order =
    m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9]
local src={data_tbl}
local t={{}} for i=1,#src do t[i]=src[i] end

-- Apply inverse layers in reverse order
{inv_body}

-- Integrity check
local h=0x811C9DC5
for i=1,#t do
    h=({bx}(h,t[i])*0x01000193)%4294967296
end
if h~=integrity_seed then return nil end

-- Convert to string
local out={{}} for i=1,#t do out[i]={sch}(t[i]) end
local s={tcat}(out)
{cache}[idx]=s
return s
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
        if 'loadstring' not in code and 'function' not in code:
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
        encrypted = encrypt_payload(payload, params)

        decoder_code, dec_name = DecoderGenerator.generate(params, encrypted, params.layer_order)

        loader = f"""
local _payload = {dec_name}(0)
if not _payload then error("Corrupted payload", 0) end
local _fn, _err = loadstring(_payload)
if not _fn then error(_err, 0) end
_fn()
"""
        full = f"""
{decoder_code}
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

    @app_commands.command(name="obf", description="Multi‑layer Luau obfuscator with randomized layer order")
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
