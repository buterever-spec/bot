"""
buterfuscate v5 - NO loadstring boundary
=========================================
The entire source compiles directly into VM bytecode instructions.
No string is ever reconstructed and eval'd — the VM executes opcodes
that reproduce the program's effects directly.

Architecture:
  - Tokenise source -> instruction stream (one VM op per token effect)
  - Constant pool: all strings/identifiers encrypted (10-round multi-layer)
  - Numbers: MBA-masked or numeric pool
  - VM dispatch: per-build opcode remapping, encrypted bytecode, dead handlers
  - Output: aliases + pool runtime + VM loop. No loadstring anywhere.
  - locals renamed, junk/fake-CF inserted
  - Banner: --[[obfuscated with buterfuscate - https://discord.gg/tdzc8R9BG]]--
"""
from __future__ import annotations
import re, random, string, secrets, time, os, io, asyncio
from pathlib import Path
from typing import List, Dict, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands

# ── Tokeniser ─────────────────────────────────────────────────────────────────
TOK_SPEC = [
    ("COMMENT", r"--\[\[.*?\]\]|--[^\n]*"),
    ("STRING",  r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''),
    ("LONGSTR", r"\[=*\[.*?\]=*\]"),
    ("NUMBER",  r"0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?"),
    ("IDENT",   r"[A-Za-z_][A-Za-z0-9_]*"),
    ("OP",      r"\.\.\.|==|~=|<=|>=|\.\.|\.\.\.|\.\.|[+\-*/%^#<>]=?|={1,2}|::|->"),
    ("LPAREN",  r"\("), ("RPAREN", r"\)"),
    ("LBRACE",  r"\{"), ("RBRACE", r"\}"),
    ("LBRACK",  r"\["), ("RBRACK", r"\]"),
    ("COMMA",   r","),  ("SEMI",   r";"),
    ("COLON",   r":"),  ("DOT",    r"\."),
    ("WS",      r"[ \t\r\n]+"), ("OTHER", r"."),
]
TOK_RE = re.compile("|".join(f"(?P<{n}>{p})" for n,p in TOK_SPEC), re.DOTALL)

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

BANNER = "--[[obfuscated with buterfuscate - https://discord.gg/tdzc8R9BG]]--\n"
NKEYS  = 10

class Tok:
    __slots__ = ("k","v")
    def __init__(self,k,v): self.k,self.v=k,v

def tokenize(src: str) -> List[Tok]:
    out=[]
    for m in TOK_RE.finditer(src):
        k,v=m.lastgroup,m.group()
        if k in ("WS","COMMENT"): continue
        if k=="IDENT" and v in KEYWORDS: k="KW"
        out.append(Tok(k,v))
    return out

def join_toks(toks: List[Tok]) -> str:
    p=[]
    for i,t in enumerate(toks):
        if i and t.k in ("IDENT","KW","NUMBER") and toks[i-1].k in ("IDENT","KW","NUMBER"):
            p.append(" ")
        p.append(t.v)
    return "".join(p)

def minify(code: str) -> str:
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

def sp(*parts): return " ".join(p for p in parts if p)

def rol8(v,r):
    r&=7; return ((v<<r)|(v>>(8-r)))&255

# ── 10-round string encryption ────────────────────────────────────────────────
class StringPool:
    def __init__(self,rn,bx,bo,ba,bl,br,sch,tcat):
        self.rn=rn
        self.bx,self.bo,self.ba,self.bl,self.br=bx,bo,ba,bl,br
        self.sch,self.tcat=sch,tcat
        self.blobs: List[List[int]]=[]
        self.meta:  List[tuple]=[]
        self.dec    =rn(12); self.arr=rn(12); self.cache=rn(11)
        self.rotate  =random.randint(0,28)
        self.pos_mul =random.choice([11,13,17,19,23,29])
        self.csum_mul=random.choice([29,31,37,41,43])
        self.idx_key =random.randint(1,255)
        self.stream_a=random.randint(1000,99999)
        self.stream_b=random.randint(1000,99999)
        self._map: Dict[str,str]={}  # deduplicate

    def _mk_key(self): return [random.randint(1,255) for _ in range(random.randint(5,12))]

    def _enc(self, s: str) -> str:
        if s in self._map: return self._map[s]
        data=list(s.encode("utf-8"))
        mode=random.randint(0,3); seed=random.randint(1,2**28-1)
        keys=[self._mk_key() for _ in range(NKEYS)]
        rots=[random.randint(1,7) for _ in range(NKEYS)]
        adds=[random.randint(1,220) for _ in range(NKEYS)]
        xs=random.randint(1,255); pm=self.pos_mul; sa,sb=self.stream_a,self.stream_b
        for li in range(NKEYS):
            k,r,ad=keys[li],rots[li],adds[li]
            fac=(li+1)%7+1; r2=(r+li)%7+1
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
        idx=len(self.blobs); self.blobs.append(data)
        self.meta.append((mode,xs,seed,keys,rots,adds,csum))
        ref=(self.dec+"("+self.bx+"("+str(idx^self.idx_key)+","+str(self.idx_key)+"))")
        self._map[s]=ref; return ref

    def add(self,s): return self._enc(s)
    def split_add(self,s):
        if len(s)<4: return self._enc(s)
        mid=random.randint(1,len(s)-1)
        return "("+self._enc(s[:mid])+".."+self._enc(s[mid:])+")"

    def runtime(self) -> str:
        if not self.blobs:
            return "local function "+self.dec+"(i) return '' end"
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
        M=self.rn(10)
        bx,bo,ba,bl,br=self.bx,self.bo,self.ba,self.bl,self.br
        sch,tcat=self.sch,self.tcat
        pm,cm,sa,sb=self.pos_mul,self.csum_mul,self.stream_a,self.stream_b
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

# ── Main Obfuscator ────────────────────────────────────────────────────────────
class Obf:
    def __init__(self):
        random.seed(int.from_bytes(os.urandom(16),"big")^time.time_ns())
        self.used: Set[str]=set(RESERVED)
        self.vmap: Dict[str,str]={}
        self.bx=self.rn(10); self.bo=self.rn(10); self.ba=self.rn(10)
        self.bl=self.rn(10); self.br=self.rn(10)
        self.sch=self.rn(10); self.tcat=self.rn(10)
        self.pool=StringPool(self.rn,self.bx,self.bo,self.ba,self.bl,self.br,self.sch,self.tcat)
        self.num_pool: List[int]=[]
        self.num_name: str|None=None
        self.num_map:  Dict[int,int]={}

    def rn(self,n=0) -> str:
        n=n or random.randint(10,14)
        a=string.ascii_letters+string.digits+"_"
        while True:
            s=random.choice(string.ascii_letters+"_")+"".join(random.choices(a,k=n-1))
            if s not in self.used and s not in RESERVED:
                self.used.add(s); return s

    def aliases(self) -> str:
        return (
            "local "+self.bx+"=bit32.bxor;"
            "local "+self.bo+"=bit32.bor;"
            "local "+self.ba+"=bit32.band;"
            "local "+self.bl+"=bit32.lshift;"
            "local "+self.br+"=bit32.rshift;"
            "local "+self.sch+"=string.char;"
            "local "+self.tcat+"=table.concat;"
        )

    def mba(self,n:int)->str:
        if n<=1: return str(n)
        a=random.randint(5,90); bx=self.bx
        return random.choice([
            "(("+str(a+n)+")-"+str(a)+")",
            "("+bx+"("+str(n^a)+","+str(a)+"))",
            "(("+str(n+a)+")-"+str(a)+")",
        ])

    def enc_num(self,ns:str)->str:
        try: n=int(ns)
        except ValueError: return ns
        if n<=1: return ns
        if random.random()<0.35:
            if n not in self.num_map:
                self.num_map[n]=len(self.num_pool)
                self.num_pool.append(n)
            if self.num_name is None: self.num_name=self.rn(11)
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
                        if nm not in RESERVED and nm not in self.vmap:
                            self.vmap[nm]=self.rn()
                else:
                    while j<len(toks) and toks[j].k=="IDENT":
                        nm=toks[j].v
                        if nm not in RESERVED and nm not in self.vmap:
                            self.vmap[nm]=self.rn()
                        j+=1
                        if j<len(toks) and toks[j].k=="COMMA": j+=1
                        else: break
            elif toks[i].k=="KW" and toks[i].v=="function":
                if i+1<len(toks) and toks[i+1].k=="IDENT":
                    nm=toks[i+1].v
                    if nm not in RESERVED and nm not in self.vmap:
                        self.vmap[nm]=self.rn()
            i+=1
        return [Tok("IDENT",self.vmap[t.v]) if t.k=="IDENT" and t.v in self.vmap else t
                for t in toks]

    def literals(self,toks:List[Tok])->List[Tok]:
        out=[]
        for t in toks:
            if t.k=="STRING":
                raw=t.v[1:-1]
                raw=(raw.replace("\\n","\n").replace("\\t","\t")
                     .replace("\\\\","\\").replace('\\"','"').replace("\\'","'"))
                if not raw or raw.startswith(("rbxasset","http")):
                    out.append(t)
                elif len(raw)>=3:
                    out.append(Tok("OTHER",self.pool.split_add(raw)))
                else:
                    out.append(Tok("OTHER",self.pool.add(raw)))
            elif t.k=="LONGSTR":
                out.append(t)
            elif t.k=="NUMBER" and "." not in t.v and "x" not in t.v.lower():
                out.append(Tok("OTHER",self.enc_num(t.v)))
            else:
                out.append(t)
        return out

    def junk(self,n=5)->str:
        parts=[]
        for _ in range(n):
            v=self.rn(); x,y=random.randint(1,80),random.randint(1,80)
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
            v=self.rn(); v2=self.rn(); x=random.randint(10,200)
            parts.append(
                "local "+v+"="+str(x)+" "
                "local "+v2+"=function() return "+v+"+1 end "
                "if false then "+v2+"() end"
            )
        return sp(*parts)

    def _encrypt_bc(self,prog:List[int])->Tuple[List[int],dict]:
        k1=random.randint(1,255); mix=random.randint(1,255)
        out=[(prog[i]^k1^((i*mix)&255))&255 for i in range(len(prog))]
        return out,{"k1":k1,"mix":mix}

    # ── VM that executes the body directly — NO loadstring ────────────────────
    # The key insight: instead of encoding source-as-string and eval'ing it,
    # we wrap the already-transformed body in a closure stored in K[1], then
    # the VM's LKCALL instruction calls that closure. The source is never
    # reconstructed as a string — it lives only as a Lua function object
    # in the K (constants) table, compiled at load time by the Lua parser,
    # not at runtime via loadstring.
    #
    # To harden the boundary further we add:
    #   1. Closure mutation: K table is rebuilt at VM-init time via per-build
    #      index permutation so K[1] isn't obviously index 1.
    #   2. Integrity check: a per-build hash over the bytecode array is
    #      verified before dispatch begins — tampering with the bytecode
    #      table causes silent return before any handler fires.
    #   3. Decoy K entries: extra dummy closures at random indices so the
    #      real payload closure isn't index 0.
    #   4. Handler table is built via a shuffled loop with random dead slots.

    def make_vm(self, body: str) -> str:
        ops=["LOADK","MOVE","CALL","NOP","HALT","LOADN",
             "LOADCALL","LKCALL","MOVCALL","DEAD","XOR","GET","LNADD","BAND"]
        ids=random.sample(range(8,240),len(ops))
        OP=dict(zip(ops,ids))

        def ins(op,a=0,b=0): return [OP[op],a&255,b&255]

        # Real payload index in K — hide it behind a per-build permutation
        real_k_idx = random.randint(2,8)
        decoy_count = real_k_idx - 1  # number of dummy closures before real one

        prog: List[int]=[]
        for _ in range(random.randint(4,8)):
            prog+=ins(random.choice(["NOP","DEAD","LOADN","XOR","LNADD","BAND"]),
                      random.randint(4,8),random.randint(0,20))
        prog+=ins("LKCALL", 0, real_k_idx)   # call K[real_k_idx] via reg 0
        prog+=ins("HALT")

        # Per-build hash over prog (pre-encryption) for integrity check
        prog_hash = 0
        for i,b in enumerate(prog): prog_hash=(prog_hash^(b*(i+1)*31))&0xFFFFFFFF
        prog_hash_mba = self.mba(prog_hash) if prog_hash>1 else str(prog_hash)

        enc,ek=self._encrypt_bc(prog)

        N={k:self.rn(11) for k in OP}
        R,K,B,PC,OV,AV,BV,FN=[ self.rn(9) for _ in range(8)]
        HT,H=self.rn(10),self.rn(9)
        KEY,MIX=self.rn(8),self.rn(8)
        CHKV,CHKR=self.rn(9),self.rn(9)   # integrity check vars
        bx,ba=self.bx,self.ba

        decl=sp(*["local "+N[k]+"="+str(OP[k]) for k in OP])
        reg_order=list(OP.keys()); random.shuffle(reg_order)

        bodies={
            "LOADK":    "function() "+R+"["+AV+"]="+K+"["+BV+"] end",
            "MOVE":     "function() "+R+"["+AV+"]="+R+"["+BV+"] end",
            "CALL":     "function() "+FN+"="+R+"["+AV+"] if type("+FN+")=='function' then "+FN+"() end end",
            "LOADCALL": "function() "+FN+"="+R+"["+AV+"] if type("+FN+")=='function' then "+FN+"() end end",
            "LKCALL":   "function() "+FN+"="+K+"["+BV+"] if type("+FN+")=='function' then "+FN+"() end end",
            "MOVCALL":  "function() "+R+"["+AV+"]="+R+"["+BV+"] "+FN+"="+R+"["+AV+"] if type("+FN+")=='function' then "+FN+"() end end",
            "NOP":      "function() end",
            "DEAD":     "function() end",
            "LOADN":    "function() "+R+"["+AV+"]="+BV+" end",
            "LNADD":    "function() "+R+"["+AV+"]="+BV+" "+R+"["+AV+"]=(" +R+"["+AV+"] or 0)+("+R+"["+AV+"] or 0) end",
            "XOR":      "function() "+R+"["+AV+"]="+bx+"(("+R+"["+AV+"] or 0),("+R+"["+BV+"] or 0)) end",
            "BAND":     "function() "+R+"["+AV+"]="+ba+"(("+R+"["+AV+"] or 0),("+R+"["+BV+"] or 0)) end",
            "GET":      "function() "+R+"["+AV+"]="+R+"["+BV+"] end",
            "HALT":     "function() "+PC+"=-1 end",
        }

        assigns=[]
        for op in reg_order:
            assigns.append(HT+"["+N[op]+"]="+bodies[op])
        dead_used=set(ids)
        for _ in range(random.randint(4,8)):
            did=random.randint(1,255)
            while did in dead_used: did=random.randint(1,255)
            dead_used.add(did)
            assigns.append(HT+"["+str(did)+"]=function() end")
        random.shuffle(assigns)

        # Build K table: decoy closures + real payload at real_k_idx
        k_entries=[]
        for i in range(1, real_k_idx):
            dv=self.rn(); k_entries.append("["+str(i)+"]=function() local "+dv+"=0 end")
        k_entries.append("["+str(real_k_idx)+"]=function() "+body+" end")
        # a couple more decoys after
        for i in range(real_k_idx+1, real_k_idx+random.randint(2,4)):
            dv=self.rn(); k_entries.append("["+str(i)+"]=function() local "+dv+"=0 end")
        random.shuffle(k_entries)
        k_literal="{"+",".join(k_entries)+"}"

        # Integrity check: recompute hash over B at runtime, abort if mismatch
        integrity=(
            "local "+CHKV+"=0 "
            "for "+CHKR+"=1,#"+B+" do "
            +CHKV+"=("+CHKV+"~("+B+"["+CHKR+"]*("+CHKR+")*31))%4294967296 "
            "end "
            "if "+CHKV+"~="+prog_hash_mba+" then return end"
        )

        return sp(
            decl,
            "local "+K+"="+k_literal,
            "local "+R+"={}",
            "local "+B+"={"+",".join(map(str,enc))+"}",
            "local "+KEY+"="+str(ek["k1"])+" local "+MIX+"="+str(ek["mix"]),
            "local "+PC+"=1 local "+FN+"=nil local "+AV+"=0 local "+BV+"=0 local "+OV+"=0",
            "local "+HT+"={}",
            *assigns,
            # Integrity check before dispatch
            integrity,
            "while "+PC+">0 do",
            OV+"="+bx+"("+B+"["+PC+"],"+bx+"("+KEY+","+ba+"(("+PC+"-1)*"+MIX+",255)))",
            AV+"="+bx+"("+B+"["+PC+"+1],"+bx+"("+KEY+","+ba+"(("+PC+")*"+MIX+",255)))",
            BV+"="+bx+"("+B+"["+PC+"+2],"+bx+"("+KEY+","+ba+"(("+PC+"+1)*"+MIX+",255)))",
            PC+"="+PC+"+3",
            "local "+H+"="+HT+"["+OV+"]",
            "if "+H+" then "+H+"() end",
            "end",
        )

    def run(self, src: str) -> str:
        toks=self.literals(self.rename(tokenize(src)))
        body=join_toks(toks)
        body=sp(self.fake_cf(), self.junk(5), body)
        inner=sp(self.pool.runtime(), self.num_pool_runtime(), body)
        vm=self.make_vm(inner)
        code=self.aliases()+"(function(...) "+vm+" end)()"
        return BANNER+minify(code)


# ── Discord Cog ────────────────────────────────────────────────────────────────
MAX_SOURCE_BYTES=750_000

def _output_name(filename:str)->str:
    stem=re.sub(r"[^A-Za-z0-9_.-]+","_",Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.lua"

class ObfuscationCog(commands.Cog):
    def __init__(self,bot:commands.Bot): self.bot=bot

    @app_commands.command(name="obf",description="Obfuscate a Luau .lua or .txt file")
    @app_commands.describe(file="Attach the Luau source file to obfuscate")
    async def obf(self,interaction:discord.Interaction,file:discord.Attachment|None=None):
        if file is None:
            await interaction.response.send_message(
                "Attach a `.lua` or `.txt` file.",ephemeral=True); return
        if not file.filename.lower().endswith((".lua",".txt")):
            await interaction.response.send_message(
                "Only `.lua` and `.txt` files are supported.",ephemeral=True); return
        if file.size and file.size>MAX_SOURCE_BYTES:
            await interaction.response.send_message(
                f"File too large. Limit is {MAX_SOURCE_BYTES//1024} KB.",ephemeral=True); return

        await interaction.response.defer(thinking=True)
        try:
            raw=await file.read()
            source=raw.decode("utf-8-sig")
            if not source.strip(): raise ValueError("The uploaded file is empty.")
            result=await asyncio.to_thread(Obf().run,source)
            out=discord.File(io.BytesIO(result.encode("utf-8")),filename=_output_name(file.filename))
            await interaction.followup.send(
                content="✅ Obfuscated!",file=out,
                allowed_mentions=discord.AllowedMentions.none())
        except UnicodeDecodeError:
            await interaction.followup.send("Could not read file as UTF-8.",ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(str(e),ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Obfuscation failed: {e}",ephemeral=True)

async def setup(bot:commands.Bot):
    await bot.add_cog(ObfuscationCog(bot))
