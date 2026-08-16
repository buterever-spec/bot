"""
buterfuscate – single-file Discord cog
=======================================
/obf      – simple obfuscation (rename + string encrypt + minify)
/obfprem  – premium obfuscation (full VM, opaque dispatch, no loadstring)
             role-gated: requires role ID 1538046325390577735

VM semantics improvement (v6):
  Instead of "decode a byte array and call a function", the VM uses a
  REGISTER-BASED THREADED DISPATCH model:
    • Each instruction is a single integer packed as:
        bits  0- 5  → opcode  (64 possible, remapped per-build)
        bits  6-13  → reg A   (0-255)
        bits 14-21  → reg B   (0-255)
        bits 22-29  → reg C / immediate low byte
    • The dispatch table maps opcode→handler closure directly
    • Handlers read A/B/C from the packed word — no separate AV/BV/OV vars
    • This means there are no obvious "fetch opcode, fetch operands, lookup
      handler" steps visible — it's just: word = stream[pc]; handlers[word%64](word)
    • The real payload closure sits at a randomised K-table index behind
      decoys; the CALL opcode computes that index via a per-build MBA expr
    • A Fibonacci-style rolling integrity check updates every iteration
      instead of a single pre-check, so partial patching is detected late
"""
from __future__ import annotations
import re, random, string, secrets, time, os, io, asyncio
from pathlib import Path
from typing import List, Dict, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants
# ─────────────────────────────────────────────────────────────────────────────
BANNER       = "--[[obfuscated with buterfuscate - https://discord.gg/tdzc8R9BG]]--\n\n"
PREM_ROLE_ID = 1538046325390577735
PREM_BYPASS_ID = 1387446299938263113
MAX_BYTES    = 750_000
NKEYS        = 10   # encryption rounds

KEYWORDS = {
    "and","break","do","else","elseif","end","false","for","function","goto",
    "if","in","local","nil","not","or","repeat","return","then","true","until",
    "while","continue",
}
RESERVED = KEYWORDS | {
    "print","warn","error","assert","type","typeof","tostring","tonumber",
    "pairs","ipairs","next","select","unpack","pcall","xpcall",
    "rawget","rawset","rawequal","setmetatable","getmetatable",
    "require","game","workspace","script","Instance","Vector3","CFrame",
    "Color3","Enum","task","wait","spawn","delay","tick","time","os","math",
    "string","table","bit32","utf8","coroutine","debug","buffer",
    "Players","ReplicatedStorage","ServerStorage","ServerScriptService",
    "StarterGui","RunService","UserInputService","HttpService",
    "DataStoreService","_G","_ENV","shared","plugin","BrickColor",
    "Lighting","TweenService","TeleportService","SoundService","Chat",
    "MarketplaceService","getfenv","setfenv","newproxy",
    "getrawmetatable","hookfunction","replaceclosure","clonefunction",
    "checkcaller","islclosure","iscclosure","loadstring","collectgarbage",
}

# ─────────────────────────────────────────────────────────────────────────────
# Tokeniser (shared)
# ─────────────────────────────────────────────────────────────────────────────
TOK_SPEC = [
    ("COMMENT", r"--\[\[.*?\]\]|--[^\n]*"),
    ("STRING",  r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''),
    ("LONGSTR", r"\[=*\[.*?\]=*\]"),
    ("NUMBER",  r"0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?"),
    ("IDENT",   r"[A-Za-z_][A-Za-z0-9_]*"),
    ("OP",      r"\.\.\.|==|~=|<=|>=|\.\.|[+\-*/%^#<>]=?|={1,2}|::"),
    ("LPAREN",  r"\("), ("RPAREN", r"\)"),
    ("LBRACE",  r"\{"), ("RBRACE", r"\}"),
    ("LBRACK",  r"\["), ("RBRACK", r"\]"),
    ("COMMA",   r","),  ("SEMI",   r";"),
    ("COLON",   r":"),  ("DOT",    r"\."),
    ("WS",      r"[ \t\r\n]+"), ("OTHER", r"."),
]
TOK_RE = re.compile("|".join(f"(?P<{n}>{p})" for n,p in TOK_SPEC), re.DOTALL)

class Tok:
    __slots__=("k","v")
    def __init__(self,k,v): self.k,self.v=k,v

def tokenize(src:str)->List[Tok]:
    out=[]
    for m in TOK_RE.finditer(src):
        k,v=m.lastgroup,m.group()
        if k in ("WS","COMMENT"): continue
        if k=="IDENT" and v in KEYWORDS: k="KW"
        out.append(Tok(k,v))
    return out

def join_toks(toks:List[Tok])->str:
    p=[]
    for i,t in enumerate(toks):
        if i and t.k in ("IDENT","KW","NUMBER") and toks[i-1].k in ("IDENT","KW","NUMBER"):
            p.append(" ")
        p.append(t.v)
    return "".join(p)

def minify(code:str)->str:
    toks=[]
    for m in TOK_RE.finditer(code):
        k,v=m.lastgroup,m.group()
        if k in ("WS","COMMENT"): continue
        if k=="IDENT" and v in KEYWORDS: k="KW"
        toks.append((k,v))
    parts=[]
    for i,(k,v) in enumerate(toks):
        if i and k in ("IDENT","KW","NUMBER") and toks[i-1][0] in ("IDENT","KW","NUMBER"):
            parts.append(" ")
        parts.append(v)
    return "".join(parts)

def sp(*parts)->str: return " ".join(p for p in parts if p)
def rol8(v,r): r&=7; return ((v<<r)|(v>>(8-r)))&255

# ─────────────────────────────────────────────────────────────────────────────
# Name generator (shared)
# ─────────────────────────────────────────────────────────────────────────────
class Namer:
    def __init__(self):
        self.used:Set[str]=set(RESERVED)
    def __call__(self,n:int=0)->str:
        n=n or random.randint(10,14)
        a=string.ascii_letters+string.digits+"_"
        while True:
            s=random.choice(string.ascii_letters+"_")+"".join(random.choices(a,k=n-1))
            if s not in self.used and s not in RESERVED:
                self.used.add(s); return s

# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  SIMPLE OBFUSCATOR  (/obf)
#  Rename locals + encrypt strings (single-layer XOR) + number MBA + minify
#  No VM, lightweight, fast, still hides intent clearly
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

def _simple_enc_str(s:str, bx_name:str, sch_name:str, tcat_name:str)->str:
    """Encode string as XOR-encrypted byte table + inline decoder."""
    key=random.randint(1,255)
    enc=[b^(key+i)%256&255 for i,b in enumerate(s.encode("utf-8"))]
    tbl="{"+",".join(map(str,enc))+"}"
    # inline: (function(t,k,r,i) r={} for i=1,#t do r[i]=sch(t[i]~(k+i-1)%256) end return tcat(r) end)(tbl,key)
    iv=f"{{"+",".join(map(str,enc))+"}"
    return (f"(function(_t,_k,_r,_i)_r={{}}for _i=1,#_t do"
            f"_r[_i]={sch_name}((_t[_i]~(_k+_i-1))%256)end return {tcat_name}(_r)end)"
            f"({tbl},{key})")

def _simple_rename(toks:List[Tok], namer:Namer)->List[Tok]:
    vmap:Dict[str,str]={}
    i=0
    while i<len(toks):
        if toks[i].k=="KW" and toks[i].v=="local":
            j=i+1
            if j<len(toks) and toks[j].k=="KW" and toks[j].v=="function":
                j+=1
                if j<len(toks) and toks[j].k=="IDENT":
                    nm=toks[j].v
                    if nm not in RESERVED and nm not in vmap: vmap[nm]=namer()
            else:
                while j<len(toks) and toks[j].k=="IDENT":
                    nm=toks[j].v
                    if nm not in RESERVED and nm not in vmap: vmap[nm]=namer()
                    j+=1
                    if j<len(toks) and toks[j].k=="COMMA": j+=1
                    else: break
        elif toks[i].k=="KW" and toks[i].v=="function":
            if i+1<len(toks) and toks[i+1].k=="IDENT":
                nm=toks[i+1].v
                if nm not in RESERVED and nm not in vmap: vmap[nm]=namer()
        i+=1
    return [Tok("IDENT",vmap[t.v]) if t.k=="IDENT" and t.v in vmap else t for t in toks]

def simple_obfuscate(src:str)->str:
    namer=Namer()
    bx=namer(10); sch=namer(10); tcat=namer(10)

    toks=_simple_rename(tokenize(src), namer)

    # Replace string literals and numbers
    out_toks=[]
    for t in toks:
        if t.k=="STRING":
            raw=t.v[1:-1]
            raw=(raw.replace("\\n","\n").replace("\\t","\t")
                 .replace("\\\\","\\").replace('\\"','"').replace("\\'","'"))
            if not raw or raw.startswith(("rbxasset","http")):
                out_toks.append(t)
            else:
                out_toks.append(Tok("OTHER",_simple_enc_str(raw,bx,sch,tcat)))
        elif t.k=="NUMBER" and "." not in t.v and "x" not in t.v.lower():
            try:
                n=int(t.v)
                if 2<=n<=9_999_999:
                    a=random.randint(5,90)
                    out_toks.append(Tok("OTHER",f"(({n+a})-{a})"))
                else:
                    out_toks.append(t)
            except ValueError:
                out_toks.append(t)
        else:
            out_toks.append(t)

    body=join_toks(out_toks)
    aliases=(f"local {bx}=bit32.bxor;"
             f"local {sch}=string.char;"
             f"local {tcat}=table.concat;")
    code=aliases+"(function(...) "+body+" end)()"
    return BANNER+minify(code)


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  PREMIUM OBFUSCATOR  (/obfprem)
#  Full VM with semantically-hidden threaded dispatch + 10-round string pool
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

# ── 10-round string encryption pool ──────────────────────────────────────────
class StringPool:
    def __init__(self,rn,bx,bo,ba,bl,br,sch,tcat):
        self.rn=rn; self.bx,self.bo,self.ba,self.bl,self.br=bx,bo,ba,bl,br
        self.sch,self.tcat=sch,tcat
        self.blobs:List[List[int]]=[]; self.meta:List[tuple]=[]
        self.dec=rn(12); self.arr=rn(12); self.cache=rn(11)
        self.rotate=random.randint(0,28)
        self.pos_mul=random.choice([11,13,17,19,23,29])
        self.csum_mul=random.choice([29,31,37,41,43])
        self.idx_key=random.randint(1,255)
        self.stream_a=random.randint(1000,99999)
        self.stream_b=random.randint(1000,99999)
        self._map:Dict[str,str]={}

    def _mk_key(self): return [random.randint(1,255) for _ in range(random.randint(5,12))]

    def _enc(self,s:str)->str:
        if s in self._map: return self._map[s]
        data=list(s.encode("utf-8"))
        mode=random.randint(0,3); seed=random.randint(1,2**28-1)
        keys=[self._mk_key() for _ in range(NKEYS)]
        rots=[random.randint(1,7) for _ in range(NKEYS)]
        adds=[random.randint(1,220) for _ in range(NKEYS)]
        xs=random.randint(1,255); pm=self.pos_mul; sa,sb=self.stream_a,self.stream_b
        for li in range(NKEYS):
            k,r,ad=keys[li],rots[li],adds[li]; fac=(li+1)%7+1; r2=(r+li)%7+1
            for i in range(len(data)): data[i]^=k[i%len(k)]
            for i in range(len(data)): data[i]=(data[i]+ad+i*fac)%256
            for i in range(len(data)): data[i]=rol8(data[i],r)
            for i in range(len(data)): data[i]^=(i*pm+xs+li*13)&255
            for i in range(len(data)): data[i]^=(seed>>((i%4)*8))&255
            for i in range(len(data)): data[i]=rol8(data[i],r2)
        st=(seed^sa)&0xFFFFFFFF
        for i in range(len(data)):
            st=(st*214013+2531011)&0xFFFFFFFF
            data[i]^=(st>>16)&255; data[i]^=(i*sb+xs)&255
        if mode>=1:
            for i in range(len(data)): data[i]=((data[i]&0x0F)<<4)|((data[i]&0xF0)>>4)
        if mode>=2: data=data[::-1]
        if mode>=3:
            for i in range(len(data)): data[i]^=(0x5A^(i*7+xs))&255
        csum=0
        for b in data: csum=(csum+b*self.csum_mul+(seed&255))&0xFFFFFFFF
        idx=len(self.blobs); self.blobs.append(data); self.meta.append((mode,xs,seed,keys,rots,adds,csum))
        ref=self.dec+"("+self.bx+"("+str(idx^self.idx_key)+","+str(self.idx_key)+"))"
        self._map[s]=ref; return ref

    def add(self,s): return self._enc(s)
    def split_add(self,s):
        if len(s)<4: return self._enc(s)
        mid=random.randint(1,len(s)-1)
        return "("+self._enc(s[:mid])+".."+self._enc(s[mid:])+")"

    def runtime(self)->str:
        if not self.blobs: return "local function "+self.dec+"(i) return '' end"
        order=list(range(len(self.blobs))); random.shuffle(order)
        r=self.rotate%max(len(order),1); order=order[r:]+order[:r]
        inv=[0]*len(self.blobs)
        for ni,oi in enumerate(order): inv[oi]=ni
        arr=",".join("{"+",".join(map(str,self.blobs[i]))+"}" for i in order)
        meta=[]
        for i,(mode,xs,seed,keys,rots,adds,csum) in enumerate(self.meta):
            ks=",".join("{"+",".join(map(str,k))+"}" for k in keys)
            rs=",".join(map(str,rots)); ads=",".join(map(str,adds))
            meta.append("{"+str(mode)+","+str(xs)+","+str(seed)+","+str(inv[i])
                        +",{"+ks+"},{"+rs+"},{"+ads+"},"+str(csum)+"}")
        M=self.rn(10); bx,bo,ba,bl,br=self.bx,self.bo,self.ba,self.bl,self.br
        sch,tcat=self.sch,self.tcat; pm,cm,sa,sb=self.pos_mul,self.csum_mul,self.stream_a,self.stream_b
        return sp(
            "local "+self.arr+"={"+arr+"}",
            "local "+M+"={"+",".join(meta)+"}",
            "local "+self.cache+"={}",
            "local function "+self.dec+"(i)",
            "if "+self.cache+"[i]~=nil then return "+self.cache+"[i] end",
            "local m="+M+"[i+1]",
            "local mode,xs,seed,pi,keys,rots,adds,expect=m[1],m[2],m[3],m[4]+1,m[5],m[6],m[7],m[8]",
            "local src="+self.arr+"[pi] local t,cs={},0",
            "for j=1,#src do t[j]=src[j] cs=(cs+src[j]*"+str(cm)+"+"+ba+"(seed,255))%4294967296 end",
            "if cs~=expect then return '' end",
            "if mode>=3 then for j=1,#t do t[j]="+bx+"(t[j],"+bx+"(90,"+ba+"((j-1)*7+xs,255))) end end",
            "if mode>=2 then local r={} for j=1,#t do r[j]=t[#t-j+1] end t=r end",
            "if mode>=1 then for j=1,#t do local v=t[j] t[j]="+bo+"("+bl+"("+ba+"(v,15),4),"+br+"("+ba+"(v,240),4)) end end",
            "local st="+bx+"(seed,"+str(sa)+")%4294967296",
            "for j=1,#t do st=(st*214013+2531011)%4294967296 "
            "t[j]="+bx+"(t[j],"+ba+"("+br+"(st,16),255)) t[j]="+bx+"(t[j],"+ba+"((j-1)*"+str(sb)+"+xs,255)) end",
            "for li="+str(NKEYS)+",1,-1 do",
            "local k=keys[li] local rr=rots[li] local ad=adds[li]",
            "local fac=(li%7)+1 local r2=((rr+(li-1))%7)+1",
            "for j=1,#t do t[j]="+bo+"("+br+"(t[j],r2),"+bl+"(t[j],8-r2)) t[j]="+ba+"(t[j],255) end",
            "for j=1,#t do t[j]="+bx+"(t[j],"+ba+"("+br+"(seed,((j-1)%4)*8),255)) end",
            "for j=1,#t do t[j]="+bx+"(t[j],"+ba+"((j-1)*"+str(pm)+"+xs+(li-1)*13,255)) end",
            "for j=1,#t do t[j]="+bo+"("+br+"(t[j],rr),"+bl+"(t[j],8-rr)) t[j]="+ba+"(t[j],255) end",
            "for j=1,#t do local v=(t[j]-ad-((j-1)*fac))%256 if v<0 then v=v+256 end t[j]=v end",
            "for j=1,#t do t[j]="+bx+"(t[j],k[((j-1)%#k)+1]) end",
            "end",
            "local out={} for j=1,#t do out[j]="+sch+"(t[j]) end",
            "local s="+tcat+"(out) "+self.cache+"[i]=s return s end",
        )


class PremObf:
    def __init__(self):
        random.seed(int.from_bytes(os.urandom(16),"big")^time.time_ns())
        self.namer=Namer()
        self.vmap:Dict[str,str]={}
        self.bx=self.namer(10); self.bo=self.namer(10); self.ba=self.namer(10)
        self.bl=self.namer(10); self.br=self.namer(10)
        self.sch=self.namer(10); self.tcat=self.namer(10)
        self.pool=StringPool(self.namer,self.bx,self.bo,self.ba,self.bl,self.br,self.sch,self.tcat)
        self.num_pool:List[int]=[]; self.num_name:str|None=None; self.num_map:Dict[int,int]={}

    def aliases(self)->str:
        return (f"local {self.bx}=bit32.bxor;"
                f"local {self.bo}=bit32.bor;"
                f"local {self.ba}=bit32.band;"
                f"local {self.bl}=bit32.lshift;"
                f"local {self.br}=bit32.rshift;"
                f"local {self.sch}=string.char;"
                f"local {self.tcat}=table.concat;")

    def mba(self,n:int)->str:
        if n<=1: return str(n)
        a=random.randint(5,90)
        return random.choice([f"(({a+n})-{a})",
                               f"({self.bx}({n^a},{a}))",
                               f"(({n+a})-{a})"])

    def enc_num(self,ns:str)->str:
        try: n=int(ns)
        except ValueError: return ns
        if n<=1: return ns
        if random.random()<0.35:
            if n not in self.num_map:
                self.num_map[n]=len(self.num_pool); self.num_pool.append(n)
            if self.num_name is None: self.num_name=self.namer(11)
            return self.num_name+"["+str(self.num_map[n]+1)+"]"
        return self.mba(n)

    def num_pool_runtime(self)->str:
        if not self.num_pool or not self.num_name: return ""
        return "local "+self.num_name+"={"+",".join(map(str,self.num_pool))+"}"

    def rename(self,toks:List[Tok])->List[Tok]:
        i=0
        while i<len(toks):
            if toks[i].k=="KW" and toks[i].v=="local":
                j=i+1
                if j<len(toks) and toks[j].k=="KW" and toks[j].v=="function":
                    j+=1
                    if j<len(toks) and toks[j].k=="IDENT":
                        nm=toks[j].v
                        if nm not in RESERVED and nm not in self.vmap: self.vmap[nm]=self.namer()
                else:
                    while j<len(toks) and toks[j].k=="IDENT":
                        nm=toks[j].v
                        if nm not in RESERVED and nm not in self.vmap: self.vmap[nm]=self.namer()
                        j+=1
                        if j<len(toks) and toks[j].k=="COMMA": j+=1
                        else: break
            elif toks[i].k=="KW" and toks[i].v=="function":
                if i+1<len(toks) and toks[i+1].k=="IDENT":
                    nm=toks[i+1].v
                    if nm not in RESERVED and nm not in self.vmap: self.vmap[nm]=self.namer()
            i+=1
        return [Tok("IDENT",self.vmap[t.v]) if t.k=="IDENT" and t.v in self.vmap else t for t in toks]

    def literals(self,toks:List[Tok])->List[Tok]:
        out=[]
        for t in toks:
            if t.k=="STRING":
                raw=t.v[1:-1]
                raw=(raw.replace("\\n","\n").replace("\\t","\t")
                     .replace("\\\\","\\").replace('\\"','"').replace("\\'","'"))
                if not raw or raw.startswith(("rbxasset","http")): out.append(t)
                elif len(raw)>=3: out.append(Tok("OTHER",self.pool.split_add(raw)))
                else: out.append(Tok("OTHER",self.pool.add(raw)))
            elif t.k=="LONGSTR": out.append(t)
            elif t.k=="NUMBER" and "." not in t.v and "x" not in t.v.lower():
                out.append(Tok("OTHER",self.enc_num(t.v)))
            else: out.append(t)
        return out

    def junk(self,n=5)->str:
        parts=[]
        for _ in range(n):
            v=self.namer(); x,y=random.randint(1,80),random.randint(1,80)
            parts.append(random.choice([
                "local "+v+"={}",
                "local "+v+"=function(...) end",
                "local "+v+"="+self.bx+"("+str(x)+","+str(y)+")",
                "if false then local "+v+"="+str(x)+" end",
                "local "+v+"=nil",
            ]))
        return sp(*parts)

    def fake_cf(self)->str:
        parts=[]
        for _ in range(random.randint(2,4)):
            v=self.namer(); v2=self.namer(); x=random.randint(10,200)
            parts.append("local "+v+"="+str(x)+" local "+v2+"=function() return "+v+"+1 end if false then "+v2+"() end")
        return sp(*parts)

    # ── Threaded register-based VM (v6) ───────────────────────────────────────
    # Instruction packing (30-bit):
    #   bits  0- 5  → opcode index into dispatch table (6 bits = 64 slots)
    #   bits  6-13  → field A  (8 bits)
    #   bits 14-21  → field B  (8 bits)
    #   bits 22-29  → field C  (8 bits)
    # Dispatch: handlers[word % SLOTS](word)
    # Each handler extracts its own A/B/C via floor/band — not pre-extracted
    # This means the dispatch loop has NO visible opcode/operand variables;
    # it's just: local w=S[p] H[w%SZ](w) p=p+1
    #
    # Rolling integrity: after every N instructions, a Fibonacci-stepped
    # accumulator is checked. Patching any instruction shifts all subsequent
    # accumulators and causes a mismatch at a random future checkpoint.

    def make_vm(self,body:str)->str:
        SLOTS=64   # dispatch table size (power of 2)

        # Logical opcodes
        OPS=["EXEC","NOP1","NOP2","NOP3","DEAD1","DEAD2"]
        # EXEC = call the payload closure; rest are junk
        n_ops=len(OPS)
        # Assign random slot indices to opcodes
        all_slots=list(range(SLOTS)); random.shuffle(all_slots)
        op_slots={op:all_slots[i] for i,op in enumerate(OPS)}
        exec_slot=op_slots["EXEC"]

        # Per-build extraction constants for A/B/C
        # A = floor(w / 64) % 256,  B = floor(w / 16384) % 256,  C = floor(w/4194304)%256
        # These divisors are powers of 2 (2^6, 2^14, 2^22) — we'll MBA-mask them
        DIV_A=64; DIV_B=16384; DIV_C=4194304

        # Build K table: payload at randomised index, decoys around it
        real_k=random.randint(3,9)
        k_entries={}
        for i in range(1,real_k):
            dv=self.namer(); k_entries[i]=f"function() local {dv}=0 end"
        k_entries[real_k]=f"function() {body} end"
        for i in range(real_k+1,real_k+random.randint(2,4)):
            dv=self.namer(); k_entries[i]=f"function() local {dv}=0 end"
        # Shuffle order in literal (indices still explicit)
        items=list(k_entries.items()); random.shuffle(items)
        k_lit="{"+",".join(f"[{ki}]={kv}" for ki,kv in items)+"}"

        # The EXEC instruction encodes real_k in field A
        # Instruction: slot=exec_slot, A=real_k, B=0, C=0
        def pack(slot,A=0,B=0,C=0):
            return (slot&63)|((A&255)<<6)|((B&255)<<14)|((C&255)<<22)

        # Build program: junk NOPs + EXEC + more junk
        prog_logical=[]
        for _ in range(random.randint(3,6)):
            prog_logical.append(pack(random.choice([op_slots["NOP1"],op_slots["NOP2"],
                                                     op_slots["NOP3"],op_slots["DEAD1"],
                                                     op_slots["DEAD2"]]),
                                     random.randint(0,255),random.randint(0,255),random.randint(0,255)))
        prog_logical.append(pack(exec_slot,real_k,0,0))
        # Trailing junk
        for _ in range(random.randint(2,5)):
            prog_logical.append(pack(random.choice([op_slots["NOP1"],op_slots["NOP2"],
                                                     op_slots["DEAD1"],op_slots["DEAD2"]]),
                                     random.randint(0,255),random.randint(0,255),random.randint(0,255)))

        # Encrypt program: each word XOR'd with LCG stream
        ek1=random.randint(1,0xFFFF); ek2=random.randint(1,0xFFFF)
        enc_prog=[]
        lcg=ek1
        for w in prog_logical:
            lcg=(lcg*1664525+1013904223)&0xFFFFFFFF
            enc_prog.append(w^(lcg&0x3FFFFFFF))  # 30-bit mask

        # Rolling integrity checkpoints
        # Every chk_interval instructions, accumulator is checked
        chk_interval=random.randint(3,5)
        # Compute expected accumulators at each checkpoint
        acc=random.randint(0x1000,0xFFFF)
        fib_a_init,fib_b_init=random.randint(1,99),random.randint(1,99)
        fib_a,fib_b=fib_a_init,fib_b_init
        checkpoints={}  # pc → expected_acc
        for i,w in enumerate(prog_logical):
            acc=(acc^w^(fib_a*(i+1)))&0xFFFFFFFF
            fib_a,fib_b=fib_b,(fib_a+fib_b)&0xFFFF
            if (i+1)%chk_interval==0:
                checkpoints[i+1]=acc
        # We'll embed checkpoints as a table {[pc]=expected,...}
        chk_entries=",".join(f"[{pc}]={v}" for pc,v in checkpoints.items())
        chk_init_acc=self.mba(list(checkpoints.values())[0]) if checkpoints else "0"

        # Variable names
        N=self.namer; rn=N
        vK=rn(9); vH=rn(9); vS=rn(9); vP=rn(9); vW=rn(9)
        vF=rn(9); vLCG=rn(8); vEK1=rn(8); vEK2=rn(8)
        vCHK=rn(9); vCHKT=rn(10); vACC=rn(9); vFIBA=rn(8); vFIBB=rn(8)
        vTMP=rn(9); vSLOTS=rn(9)
        ba=self.ba; bx=self.bx; bo=self.bo; bl=self.bl; br=self.br

        # MBA-masked constants
        mba_da=self.mba(DIV_A); mba_db=self.mba(DIV_B); mba_dc=self.mba(DIV_C)
        mba_sl=self.mba(SLOTS)
        mba_ek1=self.mba(ek1); mba_ek2=self.mba(ek2)
        mba_lcg_m=self.mba(1664525); mba_lcg_c=self.mba(1013904223)
        mba_mask=self.mba(0x3FFFFFFF)

        # Build full dispatch table:
        #   All SLOTS entries default to no-op, then real handlers overwrite
        #   EXEC handler: extracts A = floor(w/64)%256, calls K[A]
        exec_body=(f"local {vTMP}=math.floor({vW}/{mba_da})%256 "
                   f"{vF}={vK}[{vTMP}] if type({vF})=='function' then {vF}() end")

        # Build handler assignments in random order
        assigns=[]
        # Default all slots
        vDFL=rn(10)
        assigns.append(f"local {vDFL}=function({vW}) end")
        assigns.append(f"for {vTMP}=0,{mba_sl}-1 do {vH}[{vTMP}]={vDFL} end")
        # Real handlers (shuffled)
        real_assigns=[
            f"{vH}[{exec_slot}]=function({vW}) {exec_body} end",
        ]
        # Junk handlers that look real but do nothing meaningful
        used_slots={exec_slot}
        for op in ["NOP1","NOP2","NOP3","DEAD1","DEAD2"]:
            sl=op_slots[op]; used_slots.add(sl)
            dv=rn(); dv2=rn()
            real_assigns.append(f"{vH}[{sl}]=function({vW}) end")
        # Extra totally dead slots with varying patterns
        dead_slots=set()
        while len(dead_slots)<random.randint(8,14):
            s=random.randint(0,SLOTS-1)
            if s not in used_slots: dead_slots.add(s)
        for sl in dead_slots:
            dv=rn()
            real_assigns.append(f"{vH}[{sl}]=function({vW}) local {dv}=0 end")
        random.shuffle(real_assigns)
        assigns+=real_assigns

        # Checkpoint table
        chk_table=f"local {vCHKT}={{{chk_entries}}}" if checkpoints else f"local {vCHKT}={{}}"

        # Rolling integrity init
        # We store the initial acc value and re-derive; simpler: just store table and check inline
        vACC_CUR=rn(9); vFIBA2=rn(8); vFIBB2=rn(8); vIDX=rn(8)

        vm_code=sp(
            f"local {vK}={k_lit}",
            f"local {vH}={{}}",
            f"local {vS}={{{','.join(map(str,enc_prog))}}}",
            f"local {vP}=1",
            f"local {vF}=nil",
            f"local {vW}=0",
            f"local {vLCG}={mba_ek1}",
            f"local {vACC_CUR}=0",
            f"local {vFIBA2}={fib_a_init}",
            f"local {vFIBB2}={fib_b_init}",
            f"local {vIDX}=0",
            chk_table,
            *assigns,
            f"if #{vS}==0 then return end",
            f"while {vP}<=#{vS} do",
            # Decrypt instruction: XOR with LCG stream
            f"{vLCG}=({vLCG}*{mba_lcg_m}+{mba_lcg_c})%4294967296",
            f"{vW}={bx}({vS}[{vP}],{ba}({vLCG},{mba_mask}))",
            # Rolling integrity update
            f"{vIDX}={vIDX}+1",
            f"{vACC_CUR}={ba}({bx}({vACC_CUR},{bx}({vW},{ba}({vFIBA2}*{vIDX},4294967295))),4294967295)",
            f"local {vFIBA2},{vFIBB2}={vFIBB2},{ba}({vFIBA2}+{vFIBB2},65535)",
            # Check checkpoint if present
            f"if {vCHKT}[{vIDX}]~=nil and {vCHKT}[{vIDX}]~={vACC_CUR} then error(\"integrity\",0) end",
            # Dispatch
            f"local {vTMP}={vH}[{vW}%{mba_sl}]",
            f"if {vTMP} then {vTMP}({vW}) end",
            f"{vP}={vP}+1",
            "end",
        )
        # Fix: vFIBA2/vFIBB2 shadow themselves in the loop — use separate names
        # The line "local vFIBA2,vFIBB2=..." creates new locals each iter, which is fine in Lua
        return vm_code.replace(f"local {vFIBA2},{vFIBB2}={vFIBB2},{ba}({vFIBA2}+{vFIBB2},65535)",
                               f"{vFIBA2},{vFIBB2}={vFIBB2},{ba}({vFIBA2}+{vFIBB2},65535)")

    def run(self,src:str)->str:
        toks=self.literals(self.rename(tokenize(src)))
        body=join_toks(toks)
        body=sp(self.fake_cf(),self.junk(5),body)
        # Decoder lives OUTSIDE the VM closure so it's not mixed with payload
        decoder=sp(self.pool.runtime(),self.num_pool_runtime())
        vm=self.make_vm(body)
        code=self.aliases()+"(function(...) "+decoder+" "+vm+" end)()"
        return BANNER+minify(code)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────
def _out_name(filename:str)->str:
    stem=re.sub(r"[^A-Za-z0-9_.-]+","_",Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.lua"

async def _read_file(file:discord.Attachment)->str|None:
    if not file.filename.lower().endswith((".lua",".txt")):
        return None
    if file.size and file.size>MAX_BYTES:
        return None
    raw=await file.read()
    return raw.decode("utf-8-sig")

def _has_prem_role(interaction:discord.Interaction)->bool:
    member=interaction.user
    # Hardcoded bypass user — always has access
    if hasattr(member,"id") and member.id==PREM_BYPASS_ID: return True
    if not hasattr(member,"roles"): return False
    return any(r.id==PREM_ROLE_ID for r in member.roles)


# ─────────────────────────────────────────────────────────────────────────────
# Discord Cog
# ─────────────────────────────────────────────────────────────────────────────
class ObfuscationCog(commands.Cog):
    def __init__(self,bot:commands.Bot): self.bot=bot

    # ── /obf ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="obf",description="Obfuscate a Luau file (standard)")
    @app_commands.describe(file="Attach your .lua or .txt Luau source file")
    async def obf(self,interaction:discord.Interaction,file:discord.Attachment|None=None):
        if file is None:
            await interaction.response.send_message(
                "Please attach a `.lua` or `.txt` file.",ephemeral=True); return
        if not file.filename.lower().endswith((".lua",".txt")):
            await interaction.response.send_message(
                "Only `.lua` and `.txt` files are supported.",ephemeral=True); return
        if file.size and file.size>MAX_BYTES:
            await interaction.response.send_message(
                f"File too large. Limit is {MAX_BYTES//1024} KB.",ephemeral=True); return

        await interaction.response.defer(thinking=True)
        try:
            raw=await file.read()
            source=raw.decode("utf-8-sig")
            if not source.strip(): raise ValueError("The uploaded file is empty.")
            result=await asyncio.to_thread(simple_obfuscate,source)
            out=discord.File(io.BytesIO(result.encode("utf-8")),filename=_out_name(file.filename))
            await interaction.followup.send(
                content="✅ Obfuscated! *(standard)*",file=out,
                allowed_mentions=discord.AllowedMentions.none())
        except UnicodeDecodeError:
            await interaction.followup.send("Could not read file as UTF-8.",ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(str(e),ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Obfuscation failed: {e}",ephemeral=True)

    # ── /obfprem ─────────────────────────────────────────────────────────────
    @app_commands.command(name="obfprem",description="Obfuscate a Luau file (premium VM — restricted)")
    @app_commands.describe(file="Attach your .lua or .txt Luau source file")
    async def obfprem(self,interaction:discord.Interaction,file:discord.Attachment|None=None):
        # Role gate — checked before anything else
        if not _has_prem_role(interaction):
            await interaction.response.send_message(
                "🔒 You need the **Premium** role to use `/obfprem`.",
                ephemeral=True); return

        if file is None:
            await interaction.response.send_message(
                "Please attach a `.lua` or `.txt` file.",ephemeral=True); return
        if not file.filename.lower().endswith((".lua",".txt")):
            await interaction.response.send_message(
                "Only `.lua` and `.txt` files are supported.",ephemeral=True); return
        if file.size and file.size>MAX_BYTES:
            await interaction.response.send_message(
                f"File too large. Limit is {MAX_BYTES//1024} KB.",ephemeral=True); return

        await interaction.response.defer(thinking=True)
        try:
            raw=await file.read()
            source=raw.decode("utf-8-sig")
            if not source.strip(): raise ValueError("The uploaded file is empty.")
            result=await asyncio.to_thread(PremObf().run,source)
            out=discord.File(io.BytesIO(result.encode("utf-8")),filename=_out_name(file.filename))
            await interaction.followup.send(
                content="✅ Obfuscated! *(premium VM)*",file=out,
                allowed_mentions=discord.AllowedMentions.none())
        except UnicodeDecodeError:
            await interaction.followup.send("Could not read file as UTF-8.",ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(str(e),ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Obfuscation failed: {e}",ephemeral=True)


async def setup(bot:commands.Bot):
    await bot.add_cog(ObfuscationCog(bot))
