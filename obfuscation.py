"""
Discord Cog: Pirate‑style obfuscator (heavy encryption + loadstring)
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
import zlib
from pathlib import Path
from typing import List, Dict, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands

# -----------------------------------------------------------------------------
# OBFUSCATOR CORE – Pirate style
# -----------------------------------------------------------------------------

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

class PirateObfuscator:
    def __init__(self):
        random.seed(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())
        self.used: Set[str] = set()
        self.aliases: Dict[str, str] = {}

    def rn(self, length: int = 0) -> str:
        length = length or random.randint(8, 14)
        chars = string.ascii_letters + string.digits + "_"
        while True:
            name = random.choice(string.ascii_letters + "_") + ''.join(random.choices(chars, k=length-1))
            if name not in self.used and name not in RESERVED:
                self.used.add(name)
                return name

    def minify(self, code: str) -> str:
        # Remove comments and extra whitespace
        # Simple regex-based removal (not full tokenizer, but enough)
        code = re.sub(r'--\[\[.*?\]\]|--[^\n]*', '', code, flags=re.DOTALL)
        code = re.sub(r'\s+', ' ', code).strip()
        return code

    def encrypt_payload(self, data: bytes) -> Tuple[List[int], Dict]:
        """Encrypt bytes with multiple passes, return encrypted bytes and metadata."""
        # Convert to list of ints
        payload = list(data)
        n = len(payload)
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
        rotate = random.randint(0, 28)

        # Apply encryption passes (mirroring the example)
        for li in range(NKEYS):
            k, r, ad = keys[li], rots[li], adds[li]
            fac = (li + 1) % 7 + 1
            r2 = (r + li) % 7 + 1
            for i in range(len(payload)):
                payload[i] ^= k[i % len(k)]
            for i in range(len(payload)):
                payload[i] = (payload[i] + ad + i * fac) % 256
            for i in range(len(payload)):
                payload[i] = ((payload[i] << r) | (payload[i] >> (8 - r))) & 255
            for i in range(len(payload)):
                payload[i] ^= (i * pos_mul + xs + li * 13) & 255
            for i in range(len(payload)):
                payload[i] ^= (seed >> ((i % 4) * 8)) & 255
            for i in range(len(payload)):
                payload[i] = ((payload[i] << r2) | (payload[i] >> (8 - r2))) & 255

        # Additional stream
        st = (seed ^ stream_a) & 0xFFFFFFFF
        for i in range(len(payload)):
            st = (st * 214013 + 2531011) & 0xFFFFFFFF
            payload[i] ^= (st >> 16) & 255
            payload[i] ^= (i * stream_b + xs) & 255

        # Final transformations
        if mode >= 1:
            for i in range(len(payload)):
                payload[i] = ((payload[i] & 0x0F) << 4) | ((payload[i] & 0xF0) >> 4)
        if mode >= 2:
            payload = payload[::-1]
        if mode >= 3:
            for i in range(len(payload)):
                payload[i] ^= (0x5A ^ (i * 7 + xs)) & 255

        # Checksum
        csum = 0
        for b in payload:
            csum = (csum + b * csum_mul + (seed & 255)) & 0xFFFFFFFF

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
            'rotate': rotate,
            'n': n,
            'NKEYS': NKEYS,
        }
        return payload, meta

    def generate_decoder(self, encrypted: List[int], meta: Dict) -> str:
        """Generate Lua decoder code that reverses the encryption."""
        # Names
        bx = self.rn()
        bo = self.rn()
        ba = self.rn()
        bl = self.rn()
        br = self.rn()
        sch = self.rn()
        tcat = self.rn()
        dec = self.rn()
        cache = self.rn()
        meta_tbl = self.rn()
        data_tbl = self.rn()

        # Build the data table
        # We'll split the encrypted bytes into chunks (like the example)
        # The example uses a table with multiple sub-tables. We'll do the same.
        data = encrypted
        # We can use a single table of bytes, but to match the style, we'll split into chunks
        # For simplicity, we'll create one big table.
        data_str = "{" + ",".join(map(str, data)) + "}"

        # Meta table
        # We'll store: {mode, xs, seed, pi, keys, rots, adds, csum}
        # keys is a table of tables, rots and adds are tables.
        keys_str = "{" + ",".join("{" + ",".join(map(str, k)) + "}" for k in meta['keys']) + "}"
        rots_str = "{" + ",".join(map(str, meta['rots'])) + "}"
        adds_str = "{" + ",".join(map(str, meta['adds'])) + "}"
        meta_entry = "{" + ",".join([
            str(meta['mode']),
            str(meta['xs']),
            str(meta['seed']),
            "1",  # pi = 1 (index of data table)
            keys_str,
            rots_str,
            adds_str,
            str(meta['csum'])
        ]) + "}"
        meta_str = "{" + meta_entry + "}"  # Only one entry

        # Decoder function
        NKEYS = meta['NKEYS']
        pm = meta['pos_mul']
        cm = meta['csum_mul']
        sa = meta['stream_a']
        sb = meta['stream_b']

        decoder = f"""
local {bx},{bo},{ba},{bl},{br}=bit32.bxor,bit32.bor,bit32.band,bit32.lshift,bit32.rshift
local {sch},{tcat}=string.char,table.concat
local {data_tbl}={data_str}
local {meta_tbl}={meta_str}
local {cache}={{}}
local function {dec}(i)
if {cache}[i]~=nil then return {cache}[i] end
local m={meta_tbl}[i+1]
local mode,xs,seed,pi,keys,rots,adds,expect=m[1],m[2],m[3],m[4]+1,m[5],m[6],m[7],m[8]
local src={data_tbl} -- pi is always 1
local t,cs={{}},0
for j=1,#src do t[j]=src[j] cs=(cs+src[j]*{cm}+{ba}(seed,255))%4294967296 end
if cs~=expect then return '' end
if mode>=3 then for j=1,#t do t[j]={bx}(t[j],{bx}(90,{ba}((j-1)*7+xs,255))) end end
if mode>=2 then local r={{}} for j=1,#t do r[j]=t[#t-j+1] end t=r end
if mode>=1 then for j=1,#t do local v=t[j] t[j]={bo}({bl}({ba}(v,15),4),{br}({ba}(v,240),4)) end end
local st={bx}(seed,{sa})%4294967296
for j=1,#t do st=(st*214013+2531011)%4294967296 t[j]={bx}(t[j],{ba}({br}(st,16),255)) t[j]={bx}(t[j],{ba}((j-1)*{sb}+xs,255)) end
for li={NKEYS},1,-1 do
local k=keys[li] local rr=rots[li] local ad=adds[li]
local fac=(li%7)+1 local r2=((rr+(li-1))%7)+1
for j=1,#t do t[j]={bo}({br}(t[j],r2),{bl}(t[j],8-r2)) t[j]={ba}(t[j],255) end
for j=1,#t do t[j]={bx}(t[j],{ba}({br}(seed,((j-1)%4)*8),255)) end
for j=1,#t do t[j]={bx}(t[j],{ba}((j-1)*{pm}+xs+(li-1)*13,255)) end
for j=1,#t do t[j]={bo}({br}(t[j],rr),{bl}(t[j],8-rr)) t[j]={ba}(t[j],255) end
for j=1,#t do local v=(t[j]-ad-((j-1)*fac))%256 if v<0 then v=v+256 end t[j]=v end
for j=1,#t do t[j]={bx}(t[j],k[((j-1)%#k)+1]) end
end
local out={{}} for j=1,#t do out[j]={sch}(t[j]) end
local s={tcat}(out) {cache}[i]=s return s end
"""
        return decoder, dec

    def obfuscate(self, source: str) -> str:
        # 1. Minify the source
        minified = self.minify(source)
        # If empty, just return header
        if not minified.strip():
            return HEADER + "\n" + minified

        # 2. Encrypt the minified source as bytes (UTF-8)
        payload_bytes = minified.encode('utf-8')
        encrypted, meta = self.encrypt_payload(payload_bytes)

        # 3. Generate decoder
        decoder_code, dec_name = self.generate_decoder(encrypted, meta)

        # 4. Build runner: decode the payload and loadstring
        # We'll call the decoder with index 0 (since we only have one payload)
        run = f"""
local _payload={dec_name}(0)
local _fn,_err=loadstring(_payload)
if not _fn then error(_err,0) end
_fn()
"""

        # 5. Add a table layer and junk (like the example)
        env_name = self.rn()
        t1_name = self.rn()
        t2_name = self.rn()
        k1 = self.rn()
        k2 = self.rn()
        table_layer = f"""
local {env_name}=(function() local ok,e=pcall(function() return getfenv and getfenv() end) if ok and type(e)=='table' then return e end return _ENV or _G or {{}} end)()
local {t1_name}={{}} local {t2_name}={{}} 
local {k1}='{self.rn()}' local {k2}='{self.rn()}' 
{t1_name}[{k1}]=print {t1_name}[{k2}]=type 
{t2_name}[1]={t1_name} {t2_name}[2]={env_name}
"""

        # Add some junk local assignments
        junk = []
        for _ in range(random.randint(2, 5)):
            v = self.rn()
            x = random.randint(1, 80)
            junk.append(f"local {v}={x}")
        junk_code = "\n".join(junk)

        # Combine everything
        full = f"""
{decoder_code}
{table_layer}
{junk_code}
{run}
"""

        # Wrap in IIFE with bit32 aliases at the top
        # But decoder_code already has bit32 aliases, so we can just put it all in a function.
        # The example had aliases outside the function, but we'll put them inside.
        final = f"(function() {full} end)()"

        # Minify the final output (remove extra whitespace)
        final = re.sub(r'\s+', ' ', final).strip()

        # Add header
        return HEADER + "\n" + final


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

    @app_commands.command(name="obf", description="Obfuscate a Luau file with pirate‑style encryption")
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

            obf = PirateObfuscator()
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
# SETUP
# -----------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
