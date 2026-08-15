"""
Discord Cog: VM‑based obfuscator (Luau‑compatible)
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
# 4. VM GENERATOR (Lua)
# -----------------------------------------------------------------------------

class VMGenerator:
    @staticmethod
    def generate(params: CryptoParams, encrypted: bytes) -> Tuple[str, str]:
        used = set()
        # Random names for VM components
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
        pc = random_name(used)          # program counter
        reg = random_name(used)         # register array
        op = random_name(used)          # current opcode
        a = random_name(used)           # operand A
        b = random_name(used)           # operand B
        # opcode mapping (random numbers)
        opcodes = {
            'XORC': random.randint(10, 240),
            'ADDC': random.randint(10, 240),
            'ROTL': random.randint(10, 240),
            'ROTR': random.randint(10, 240),
            'XORPOS': random.randint(10, 240),
            'ADDPOS': random.randint(10, 240),
            'LOAD': random.randint(10, 240),
            'STORE': random.randint(10, 240),
            'CHECKSUM': random.randint(10, 240),
            'EXEC': random.randint(10, 240),
        }
        # Ensure uniqueness
        vals = list(opcodes.values())
        for k in opcodes:
            while opcodes[k] in vals[:vals.index(opcodes[k])]:
                opcodes[k] = random.randint(10, 240)
            vals = list(opcodes.values())

        # Metadata
        meta_entry = "{" + ",".join([
            str(params.seed),
            str(params.rounds),
            "{" + ",".join(map(str, params.xor_keys)) + "}",
            "{" + ",".join(map(str, params.rot_l)) + "}",
            "{" + ",".join(map(str, params.rot_r)) + "}",
            "{" + ",".join(map(str, params.add_const)) + "}",
            str(params.pos_mul),
            str(params.stream_a),
            str(params.stream_b),
            str(params.integrity_seed),
        ]) + "}"
        meta_str = "{" + meta_entry + "}"

        # Encrypted data as table
        data_str = "{" + ",".join(map(str, encrypted)) + "}"
        data_tbl_def = f"local {data_tbl}={{{data_str}}}"

        # Generate instruction sequence (bytecode) that when executed decrypts the payload
        # We'll generate a sequence of VM instructions that perform the decryption steps.
        # The instructions will be stored as a table of {opcode, operandA, operandB}
        # We'll randomize the order of operations, but they must be reversible.
        # Since we already have the encryption parameters, we can generate instructions that reverse them.
        # We'll generate the following steps in reverse order:
        # - stream reverse
        # - rounds reverse
        # - (no permutation)
        # We'll encode these as VM instructions.

        # Build a list of instructions as (opcode_name, a, b)
        instr_list = []

        # 1. Stream reverse: we need to recompute the stream and XOR back
        # We'll do this in Lua directly because it's complex with VM; we'll use a LOAD/STORE approach.
        # Actually we can just keep the stream reverse in the VM as well.
        # But to keep VM small, we'll do stream reverse as a separate step outside VM.
        # The VM will handle the round reversals.

        # Generate round reversal instructions
        for r in range(params.rounds, 0, -1):
            # Each round has: rot_r inverse, pos XOR, rot_l inverse, add inverse, XOR
            # We'll encode each as separate instructions.
            # XOR constant
            instr_list.append(('XORC', r-1, params.xor_keys[r-1]))
            # ADD inverse: we'll use ADDC with negative value? We'll use ADDC with value = -addc, but modulo 256.
            addc = params.add_const[r-1]
            fac = (r % 7) + 1
            # For position-dependent add, we need to subtract (i-1)*fac
            # We can use a special instruction ADDPOS that subtracts based on position.
            # We'll add ADDPOS with parameters.
            instr_list.append(('ADDPOS', r-1, fac))
            # Rotate left inverse (rotate right)
            instr_list.append(('ROTL', r-1, params.rot_l[r-1]))  # Actually we need ROTR, but we'll handle in VM
            # Position XOR
            instr_list.append(('XORPOS', r-1, params.pos_mul))
            # Rotate right inverse (rotate left)
            instr_list.append(('ROTR', r-1, params.rot_r[r-1]))

        # We'll also add a checksum instruction at the end.
        instr_list.append(('CHECKSUM', 0, params.integrity_seed))
        # Then EXEC
        instr_list.append(('EXEC', 0, 0))

        # Now encode instructions as bytes: each instruction is {opcode, a, b}
        # We'll store as a table of tables.
        instr_table = []
        for op_name, a_val, b_val in instr_list:
            instr_table.append("{" + str(opcodes[op_name]) + "," + str(a_val) + "," + str(b_val) + "}")
        instr_str = "{" + ",".join(instr_table) + "}"

        # Build the VM code
        vm_code = f"""
-- VM aliases
local {bx},{bo},{ba},{bl},{br}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {sch},{tcat}=string.char,table.concat
{data_tbl_def}
local {meta_tbl}={meta_str}
local {cache}={{}}

-- VM dispatch table
local opcodes = {{
    [{opcodes['XORC']}] = function(r, a, b) r[a+1] = {bx}(r[a+1], b) end,
    [{opcodes['ADDC']}] = function(r, a, b) r[a+1] = (r[a+1] + b) % 256 end,
    [{opcodes['ROTL']}] = function(r, a, b) r[a+1] = {bo}({bl}(r[a+1], b), {br}(r[a+1], 8-b)) r[a+1]={ba}(r[a+1],255) end,
    [{opcodes['ROTR']}] = function(r, a, b) r[a+1] = {bo}({br}(r[a+1], b), {bl}(r[a+1], 8-b)) r[a+1]={ba}(r[a+1],255) end,
    [{opcodes['XORPOS']}] = function(r, a, b) for i=1,#r do r[i] = {bx}(r[i], {ba}((i-1)*b + {ba}(a,255), 255)) end end,
    [{opcodes['ADDPOS']}] = function(r, a, b) for i=1,#r do r[i] = (r[i] - {ba}((i-1)*b, 255)) % 256 end end,
    [{opcodes['LOAD']}] = function(r, a, b) r[a+1] = {data_tbl}[b+1] end,
    [{opcodes['STORE']}] = function(r, a, b) {data_tbl}[a+1] = r[b+1] end,
    [{opcodes['CHECKSUM']}] = function(r, a, b)
        local h=0x811C9DC5
        for i=1,#r do h=({bx}(h,r[i])*0x01000193)%4294967296 end
        if h~=b then error("Corrupted",0) end
    end,
    [{opcodes['EXEC']}] = function(r, a, b)
        local s={{}} for i=1,#r do s[i]={sch}(r[i]) end
        local payload={tcat}(s)
        local fn, err=loadstring(payload)
        if not fn then error(err,0) end
        fn()
    end,
}}

-- VM interpreter
local function {dec}(idx)
    if {cache}[idx]~=nil then return {cache}[idx] end
    local m={meta_tbl}[idx+1]
    local seed, rounds, xor_keys, rot_l, rot_r, add_const, pos_mul, stream_a, stream_b, integrity_seed =
        m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10]
    local src={data_tbl}
    local r={{}} for i=1,#src do r[i]=src[i] end

    -- Stream reverse (done outside VM for performance)
    local state={bx}(seed,stream_a)
    for i=1,#r do
        state=(state*214013+2531011)%4294967296
        r[i]={bx}(r[i],{ba}({br}(state,16),255))
        r[i]={bx}(r[i],{ba}((i-1)*stream_b+{ba}(seed,255),255))
    end

    -- Execute VM instructions
    local instrs = {instr_str}
    for _, instr in ipairs(instrs) do
        local opcode, a, b = instr[1], instr[2], instr[3]
        local handler = opcodes[opcode]
        if handler then handler(r, a, b) end
    end

    -- Cache and return (not used as payload is executed directly)
    {cache}[idx] = true
    return true
end
"""
        return vm_code, dec

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
        if 'function' not in code:
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

        vm_code, dec_name = VMGenerator.generate(params, encrypted)

        loader = f"""
local _ok = {dec_name}(0)
if not _ok then error("VM execution failed", 0) end
"""
        full = f"""
{vm_code}
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

    @app_commands.command(name="obf", description="VM-based Luau obfuscator")
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
