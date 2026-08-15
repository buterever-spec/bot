"""
Discord Cog: VM‑based obfuscator with variable instruction sets (Luau‑compatible)
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
    code = re.sub(r'--\[\[.*?\]\]|--[^\n]*', '', code, flags=re.DOTALL)
    code = re.sub(r'\s+', ' ', code).strip()
    return code

# -----------------------------------------------------------------------------
# 2. ENCRYPTION LAYERS
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
    # Layer 5: Permutation
    if params.layer5_perm:
        data = bytearray(data[p] for p in params.layer5_perm)
    # Layer 6: Stream cipher
    state = (params.seed ^ params.stream_a) & 0xFFFFFFFF
    for i in range(len(data)):
        state = (state * 214013 + 2531011) & 0xFFFFFFFF
        data[i] ^= (state >> 16) & 0xFF
        data[i] ^= (i * params.stream_b + (params.seed & 0xFF)) & 0xFF
    return bytes(data)

# -----------------------------------------------------------------------------
# 3. VM GENERATOR – produces a variable instruction set and program
# -----------------------------------------------------------------------------

class VMGenerator:
    @staticmethod
    def generate(params: CryptoParams, encrypted: bytes) -> Tuple[str, str]:
        used = set()
        # Random names for VM locals
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

        # Metadata table (1‑based)
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

        # Define VM instruction set – we'll have operations that manipulate a register array.
        # Registers: r[1..n] holds the current data bytes.
        # We'll define opcodes for:
        # - XORC: r[a] = r[a] ^ b (b is immediate)
        # - ADDC: r[a] = (r[a] + b) % 256
        # - ROTL: r[a] = rotate_left(r[a], b)
        # - ROTR: r[a] = rotate_right(r[a], b)
        # - XORPOS: for i=1..#r do r[i] = r[i] ^ ((i-1)*b + seed_mod) end
        # - ADDPOS: for i=1..#r do r[i] = (r[i] - (i-1)*b) % 256 end
        # - LOAD: r[a] = data[b+1]  (load from encrypted data)
        # - STORE: data[a+1] = r[b]  (store back to data)
        # - CHECKSUM: verify FNV-1a
        # - EXEC: decode and run payload

        # Randomize opcode numbers (must be unique)
        ops = [
            'XORC', 'ADDC', 'ROTL', 'ROTR', 'XORPOS', 'ADDPOS', 
            'LOAD', 'STORE', 'CHECKSUM', 'EXEC'
        ]
        op_map = {}
        used_vals = set()
        for op in ops:
            val = random.randint(10, 240)
            while val in used_vals:
                val = random.randint(10, 240)
            used_vals.add(val)
            op_map[op] = val

        # Generate the instruction sequence that reverses the encryption layers.
        # The forward layers: layer1, layer2, layer3, layer4, layer5, layer6 (stream)
        # Reverse order: stream inverse, perm inverse, layer4 inverse (XOR self), layer3 inverse (subtract), layer2 inverse (rotate right), layer1 inverse (XOR self)
        # We'll generate instructions for each step.

        instr_list = []

        # 1. Stream inverse (we'll do this outside VM because it's complex; but we can do it in VM as well)
        # For simplicity, we'll do stream inverse in the main decoder before entering VM.
        # Actually we can embed the stream inverse as a series of VM instructions: we can compute state and XOR back.
        # But that's many instructions. Let's keep stream inverse in the main decoder function (outside VM) and let VM handle the rest.
        # However, to make the VM more involved, we can do stream inverse inside VM using a loop.
        # We'll generate instructions for the stream inverse: we need to recompute state and XOR.
        # This would be a loop; we can implement it as a single instruction that loops? That's not feasible.
        # We'll keep stream inverse outside VM, as before.

        # 2. Permutation inverse: we need to apply inverse permutation.
        # We can do this in VM by using LOAD and STORE with computed indices.
        # But that would be many instructions. We'll compute inverse permutation in Lua before VM.
        # Actually we can do permutation reversal using a single instruction that applies the inverse perm.
        # We'll create an instruction 'PERM' that uses the perm table.

        # Instead of making a complex VM, we'll keep the VM simple and handle permutation in Lua as well.
        # The VM will handle the round-based transforms (XORC, ADDC, ROTL, ROTR, XORPOS, ADDPOS).
        # We'll generate the instructions for the rounds in reverse order.

        # Generate round reversal instructions
        # For each round, we need to apply:
        # - layer1: XOR with single key (self-inverse) -> XORC with key
        # - layer2: rotate left (inverse is rotate right) -> ROTR with rot amount
        # - layer3: add with position factor (inverse subtract with same factor) -> ADDPOS with fac=1
        # - layer4: XOR with key sequence (self-inverse) -> XORPOS (applies to all bytes) but we need to XOR with the key sequence per position.

        # We'll generate a sequence of instructions for each byte position, but that would be too many.
        # Instead, we'll use the XORPOS instruction which loops over all bytes with a computed key.
        # For layer4, we can use XORPOS with b = 0 and a = seed, and inside we use layer4_xor array.
        # We'll design XORPOS to take a parameter b that determines the XOR pattern: if b==0, use layer4_xor array.

        # Let's simplify: we'll implement the round reversal in the VM using instructions that loop over the entire array.
        # We'll create an instruction 'ROUND' that performs all steps for a given round? That would defeat the purpose of variable VM.

        # Given the complexity, we'll stick with a reliable approach: generate a VM that interprets a program that applies each transformation in the correct order using simple instructions that operate on single elements.
        # We'll generate a sequence of LOAD, XORC, ADDC, ROTL, ROTR, and STORE instructions for each byte.
        # That will make the program very long but still valid.

        # We'll generate instructions for each byte position (1..n) for each layer.
        # But that could be huge for large payloads. However, for typical scripts (a few KB) it's acceptable.
        # We'll also randomize the order of bytes processed (to break patterns).

        n = len(encrypted)
        # We'll generate a randomized order of byte indices for each layer
        # For each layer, we'll process bytes in a random permutation.

        # We'll build a program that:
        # - Loads the encrypted data into registers
        # - For each byte, applies the reverse operations in reverse order.
        # - Then runs integrity check and EXEC.

        # We'll do: for each byte i, we'll generate instructions to reverse layer1..4.
        # Reverse layer1: XOR key
        # Reverse layer2: ROTR
        # Reverse layer3: ADDPOS (subtract)
        # Reverse layer4: XOR with key sequence (self-inverse)
        # Since these operations are per-byte and independent, we can process bytes in any order.
        # We'll randomize the order for each layer to make it structurally different.

        # We'll generate a list of instructions for each layer, but we'll interleave them? To keep it simple,
        # we'll generate sequential blocks for each layer, but the order of layers can be randomized? No, the order must be correct for decryption.

        # Actually, the reverse order is: layer4 inverse, layer3 inverse, layer2 inverse, layer1 inverse.
        # So we need to apply them in that order. But we can process all bytes for a given layer, then move to the next.
        # That would produce a block of instructions for each layer. We'll randomize the order of bytes within each layer.

        # We'll generate for each layer a list of byte indices, shuffle them, and then emit instructions.

        # For layer4 inverse: XOR with key sequence (self-inverse).
        # For each byte i, emit: XORC i, key[i % len(keys)]
        # For layer3 inverse: subtract add_const + (i-1)
        # For each byte i, emit: ADDC i, -(add_const + (i-1)) modulo 256, but we'll use a special instruction that subtracts.
        # We can define SUB instruction. But we already have ADDC with negative value: (add_const + i) % 256, we can use ADDC with b = 256 - value.
        # For layer2 inverse: ROTR with rot amount.
        # For each byte i, emit: ROTR i, rot_amount
        # For layer1 inverse: XOR with single key.
        # For each byte i, emit: XORC i, key

        # We'll generate the instructions.

        # We'll need to handle permutation and stream separately (we'll do them outside VM).

        # Define helper to get byte indices list, shuffled.
        byte_indices = list(range(1, n+1))
        random.shuffle(byte_indices)

        instr_list = []
        keys = params.layer4_xor
        # Layer4 inverse (XOR with keys)
        for i in byte_indices:
            key = keys[(i-1) % len(keys)]
            instr_list.append((op_map['XORC'], i, key))
        # Layer3 inverse (subtract add_const + (i-1))
        for i in byte_indices:
            val = (params.layer3_add + (i-1)) & 0xFF
            # We want to subtract val, so we add (256 - val) modulo 256
            sub_val = (-val) & 0xFF
            instr_list.append((op_map['ADDC'], i, sub_val))
        # Layer2 inverse (rotate right)
        for i in byte_indices:
            instr_list.append((op_map['ROTR'], i, params.layer2_rot))
        # Layer1 inverse (XOR with single key)
        for i in byte_indices:
            instr_list.append((op_map['XORC'], i, params.layer1_key))

        # After all layers, we need to apply permutation inverse.
        # We'll do that outside VM as well.
        # Actually we can do permutation inverse using LOAD/STORE instructions.
        # We'll generate instructions that for each original position i, load from perm[i] and store to i.
        # We'll use the inverse permutation: inv_perm[perm[i]] = i.
        # We'll compute inv_perm in Python and generate LOAD/STORE instructions.
        if params.layer5_perm:
            inv_perm = [0] * n
            for idx, p in enumerate(params.layer5_perm):
                inv_perm[p] = idx + 1  # Lua 1-based
            # Generate instructions: for i from 1 to n, LOAD temp, inv_perm[i]; STORE i, temp
            # We'll need a temporary register index (n+1)
            temp_reg = n + 1
            for i in range(1, n+1):
                src_idx = inv_perm[i-1]
                instr_list.append((op_map['LOAD'], temp_reg, src_idx))
                instr_list.append((op_map['STORE'], i, temp_reg))

        # Add CHECKSUM and EXEC
        instr_list.append((op_map['CHECKSUM'], 0, params.integrity_seed))
        instr_list.append((op_map['EXEC'], 0, 0))

        # Now build the program table
        prog_str = "{" + ",".join("{" + str(op) + "," + str(a) + "," + str(b) + "}" for op, a, b in instr_list) + "}"

        # Build the VM dispatch table with randomized order of handlers
        # We'll create a table mapping opcode to function.
        # We'll randomize the order of handler definitions but they will be indexed by opcode.

        # Define handlers as strings
        handlers = {
            'XORC': f"function(r,a,b) r[a]=({bx}(r[a] or 0,b)) end",
            'ADDC': f"function(r,a,b) r[a]=((r[a] or 0)+b)%256 end",
            'ROTL': f"function(r,a,b) r[a]={bo}({bl}(r[a] or 0,b),{br}(r[a] or 0,8-b)) r[a]={ba}(r[a] or 0,255) end",
            'ROTR': f"function(r,a,b) r[a]={bo}({br}(r[a] or 0,b),{bl}(r[a] or 0,8-b)) r[a]={ba}(r[a] or 0,255) end",
            'XORPOS': f"function(r,a,b) for i=1,#r do r[i]={bx}(r[i],{ba}((i-1)*b+{ba}(a,255),255)) end end",
            'ADDPOS': f"function(r,a,b) for i=1,#r do r[i]=(r[i]-{ba}((i-1)*b,255))%256 end end",
            'LOAD': f"function(r,a,b) r[a]=src[b] end",
            'STORE': f"function(r,a,b) src[a]=r[b] end",
            'CHECKSUM': f"function(r,a,b) local h=0x811C9DC5 for i=1,#r do h=({bx}(h,r[i])*0x01000193)%4294967296 end if h~=b then error('corrupt') end end",
            'EXEC': f"function(r,a,b) local s={{}} for i=1,#r do s[i]={sch}(r[i]) end local p={tcat}(s) local fn,err=loadstring(p) if not fn then error(err,0) end fn() end",
        }

        # Build dispatch table
        dispatch_entries = []
        for op, val in op_map.items():
            dispatch_entries.append(f"[{val}]={handlers[op]}")
        random.shuffle(dispatch_entries)  # randomize order
        dispatch_str = "{" + ",".join(dispatch_entries) + "}"

        # Now build the VM code
        vm_code = f"""
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
local r={{}} for i=1,#src do r[i]=src[i] end

-- Reverse stream
local state={bx}(seed,stream_a)
for i=1,#r do
    state=(state*214013+2531011)%4294967296
    r[i]={bx}(r[i],{ba}({br}(state,16),255))
    r[i]={bx}(r[i],{ba}((i-1)*stream_b+{ba}(seed,255),255))
end

-- VM interpreter
local prog = {prog_str}
local dispatch = {dispatch_str}
local pc = 1
while pc <= #prog do
    local instr = prog[pc]
    local opcode = instr[1]
    local a = instr[2]
    local b = instr[3]
    local handler = dispatch[opcode]
    if handler then handler(r, a, b) end
    pc = pc + 1
end

-- Cache result (the decoded payload)
local out={{}} for i=1,#r do out[i]={sch}(r[i]) end
local payload={tcat}(out)
{cache}[idx]=payload
return payload
end
"""
        return vm_code, dec

# -----------------------------------------------------------------------------
# 4. MAIN OBFUSCATOR
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

        vm_code, dec_name = VMGenerator.generate(params, encrypted)

        loader = f"""
local _payload = {dec_name}(0)
if not _payload then error("Corrupted payload", 0) end
local _fn, _err = loadstring(_payload)
if not _fn then error(_err, 0) end
_fn()
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
# 5. DISCORD COG
# -----------------------------------------------------------------------------

MAX_SOURCE_BYTES = 750_000

def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.lua"

class Obfuscation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="obf", description="VM-based Luau obfuscator with variable instruction sets")
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
# 6. SETUP
# -----------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
