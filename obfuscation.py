import discord
from discord import app_commands
from discord.ext import commands
import io
import asyncio
import random
import base64
import re
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Set, Dict, Tuple, Optional

# --- Dependencies for Luau parsing and AST manipulation ---
# These will attempt to import the required packages.
# If they are not installed, the cog will raise an error on load.
try:
    from luaparser import ast, astnodes
    from luaparser.parser import Parser
except ImportError:
    raise ImportError("The 'luaparser' library is required. Please install it with: pip install luaparser")


# -----------------------------------------------------------------------------
# 1. STRING ENCRYPTER (from Opiens)
# -----------------------------------------------------------------------------
class StringEncrypter:
    def __init__(self, Source, Parser, StrKey):
        self.Source = Source
        self.StrKey = StrKey
        self.Parser = Parser
        self.B32Decryptor = (
            'local function a(b,c)local d={}for e=1,#b,c do table.insert(d,b:sub(e,e+c-1))end;return d end;'
            'local function f(g)local d=""repeat local h=g/2;local i,j=math.modf(h)g=i;d=math.ceil(j)..d until g==0;return d end;'
            'local k="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"'
            'local function Base32(b)local m=b:gsub(".",function(n)if n=="="then return""end;local o=string.find(k,n)o=o-1;return string.format("%05u",f(o))end)'
            'local p=a(m,8)local q={}for r,s in pairs(p)do table.insert(q,string.char(tonumber(s,2)))end;'
            'local t=table.concat(q)local u={}for e=1,#t,1 do local s=string.byte(t,e)table.insert(u,e,s)end;'
            'local v=""for e=1,#u-1,1 do local s=u[e]local w=DecryptSTR(s)v=v..string.char(w)end;return v end\n'
        )
        self.StrDecryptor = (
            "local function DecryptSTR(b)local c,d=1,0;local e={};while b>0 and e>0 do local f,g=b%2,e%2;"
            "if f~=g then d=d+c end;b=(b-f)/2;e=(e-g)/2;c=c*2 end;"
            "if b<e then b=e end;while b>0 do local f=b%2;if f>0 then d=d+c end;b=(b-f)/2;c=c*2 end; return d end;\n"
            .format(StrKey) + self.B32Decryptor
        )

    @staticmethod
    def RandomString(Len):
        return ''.join(random.choice('qwertyuioplkjhgfdsazxcvbnmQWERTYUIOPLKJHGFDSAZXCVBNM') for i in range(Len))

    @staticmethod
    def Encrypt(plaintext, key):
        PTBytes = [ord(a.encode("utf-8")) for a in plaintext]
        for a in range(len(PTBytes)):
            PTBytes[a] = PTBytes[a] ^ key
        return base64.b32encode(bytes(PTBytes)).decode('utf-8')

    def EncryptStrings(self):
        LocalCount = 0
        StringTable = []
        LocalNameTable = []
        LocalTable = []
        StringNodes = self.Parser.GetStrings()

        for mNode in StringNodes:
            if LocalCount > 100:
                break
            try:
                String = mNode.s
                LocalCount += 1
                StringTable.append(String)
                RString = self.RandomString(7)
                LocalNameTable.append(RString)
                LocalTable.append("local " + RString + " = Base32(\"" + StringEncrypter.Encrypt(String, self.StrKey) + "\")\n")
            except:
                pass

        for Idx in range(0, len(StringTable)):
            self.Source = self.Source.replace('"' + StringTable[Idx] + '"', '(' + LocalNameTable[Idx] + ')', 1)
        for Idx in range(0, len(StringTable)):
            self.Source = self.Source.replace("'" + StringTable[Idx] + "'", '(' + LocalNameTable[Idx] + ')', 1)

        RetCode = ""
        for a in LocalTable:
            RetCode += a + "\n"
        RetCode += self.Source

        return RetCode, self.StrDecryptor


# -----------------------------------------------------------------------------
# 2. MATH ENCRYPTER (from Opiens)
# -----------------------------------------------------------------------------
class MathEncrypter:
    def __init__(self, Parser, IntKey):
        self.Left = 0
        self.Right = 0
        self.Result = 0
        self.Decrypt = "local function DecryptINT(b)local c,d=1,0;local e={};while b>0 and e>0 do local f,g=b%2,e%2;if f~=g then d=d+c end;b=(b-f)/2;e=(e-g)/2;c=c*2 end;if b<e then b=e end;while b>0 do local f=b%2;if f>0 then d=d+c end;b=(b-f)/2;c=c*2 end; return d end\n".format(IntKey)
        self.IntKey = IntKey
        self.Parser = Parser

    @staticmethod
    def GetRandomString(Len):
        Alfabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        Result = ""
        for Idx in range(0, Len):
            Result += random.choice(Alfabet)
        return Result

    @staticmethod
    def TurnStringOrNumber(Num):
        Chc = random.randint(0, 1)
        if Chc:
            return "#\"" + MathEncrypter.GetRandomString(Num) + "\""
        else:
            return Num

    def EncryptMath(self):
        self.EncryptNumbers()
        return self.Parser.GetAstTree(), self.Decrypt

    def EncryptNumbers(self):
        NumberNode = []

        class NumberVisitor(ast.ASTVisitor):
            def visit_Number(self, node):
                if node.n >= 0 and type(node.n) == int:
                    NumberNode.append(node)

        NumberVisitor().visit(self.Parser.GetAstTree())

        for Idx in range(0, len(NumberNode)):
            Arguments = [astnodes.Number(NumberNode[Idx].n ^ self.IntKey)]
            ch = random.randint(0, 2)
            while not ch:
                ch = random.randint(0, 2)
                Arguments.append(astnodes.Number(random.randint(0, 100)))

            self.Parser.ReplaceValues(NumberNode[Idx], astnodes.Call(astnodes.Name("DecryptINT"), Arguments))


# -----------------------------------------------------------------------------
# 3. LOCAL TRANSFORMER (from Opiens)
# -----------------------------------------------------------------------------
class Local:
    def __init__(self, Parser, AstTree):
        self.Operator = ""
        self.Parser = Parser
        self.AstTree = AstTree

    def PutLocalOnTop(self):
        Locals = self.Parser.GetLocalAssigns()
        for Idx in range(0, len(Locals)):
            if Locals[Idx].values != []:
                TempNode = Locals[Idx]
                self.Parser.ReplaceNode(TempNode, astnodes.Assign(TempNode.targets, TempNode.values))
                self.Parser.InsertNode(astnodes.LocalAssign(TempNode.targets, astnodes.Nil()), Idx)
            elif Locals[Idx].values == []:
                TempNode = Locals[Idx]
                self.Parser.ReplaceNode(TempNode, astnodes.LocalAssign(TempNode.targets, astnodes.Nil()))

        FuncLocals = self.Parser.GetLocalFunctions()
        for Idx in range(0, len(FuncLocals)):
            if FuncLocals[Idx].name != []:
                TempNode = FuncLocals[Idx]
                self.Parser.ReplaceNode(TempNode, astnodes.Assign(TempNode.name.id, astnodes.AnonymousFunction(TempNode.args, TempNode.body)))
                self.Parser.InsertNode(astnodes.LocalAssign(TempNode.name.id, astnodes.Nil()), Idx)

        return self.Parser.GetAstTree()


# -----------------------------------------------------------------------------
# 4. MAIN OBFUSCATOR (from Opiens, adapted)
# -----------------------------------------------------------------------------
class Obfuscator:
    def __init__(self, Source, Options):
        self.Parser = Parser(Source)
        self.Options = Options
        self.AstTree = self.Parser.Parse()
        self.Source = Source
        self.IntKey = random.randint(10, 50)
        self.StrKey = random.randint(10, 50)
        self.IntDecryptor = ""
        self.StrDecryptor = ""

    def Obfuscate(self):
        self.AstTree = Local(self.Parser, self.AstTree).PutLocalOnTop()
        self.Parser.AstTree = self.AstTree

        if self.Options["Encryption"]["Integer"]:
            self.AstTree, self.IntDecryptor = MathEncrypter(self.Parser, self.IntKey).EncryptMath()
            self.Source = ast.to_lua_source(self.AstTree)

        if self.Options["Encryption"]["String"]:
            self.Source, self.StrDecryptor = StringEncrypter(self.Source, self.Parser, self.StrKey).EncryptStrings()
            self.Parser = Parser(self.Source)
            self.AstTree = self.Parser.Parse()

        self.Source = self.IntDecryptor + self.StrDecryptor + self.Source

        # If VM obfuscation is enabled, this would compile to bytecode and embed it.
        # For this standalone version, we'll skip the VM part as it requires additional
        # files from the original repo (Vm/BytecodeRW, Vm/Bytecode/Compiler, Rewriter/Flattener).
        if self.Options.get("Vm", False):
            # Placeholder for VM obfuscation logic
            pass

        return self.Source


# -----------------------------------------------------------------------------
# 5. DISCORD COG
# -----------------------------------------------------------------------------
MAX_SOURCE_BYTES = 750_000

def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.lua"

class ObfuscationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="obfuscate", description="Obfuscate a Luau source file using the Opiens Obfuscator.")
    @app_commands.describe(file="Attach the .lua or .txt Luau source file to obfuscate")
    async def obfuscate(self, interaction: discord.Interaction, file: discord.Attachment):
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

            # Define obfuscation options
            options = {
                "Encryption": {
                    "Integer": True,
                    "String": True
                },
                "Vm": False  # VM obfuscation is disabled in this standalone version
            }

            # Run obfuscation in a thread to avoid blocking the event loop
            obfuscated = await asyncio.to_thread(self._run_obfuscator, source, options)

            # Send the result
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

    def _run_obfuscator(self, source: str, options: dict) -> str:
        """Wrapper to run the obfuscator in a separate thread."""
        obf = Obfuscator(source, options)
        return obf.Obfuscate()


# -----------------------------------------------------------------------------
# 6. SETUP FUNCTION FOR THE COG
# -----------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(ObfuscationCog(bot))
