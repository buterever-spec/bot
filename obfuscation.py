# ── improved obfuscate_luau (v4) ─────────────────────────────────────────────

import ast
import random
import secrets
import re
from typing import List, Tuple, Set, Dict, Any

# ... (keep your existing imports and helper functions: _fresh, _tokens, _literal_bytes,
#      _mba, _opaque, _mask_num, _encrypt, _pack, _sep, _compact, _rename_locals,
#      _output_name, etc.)  but replace obfuscate_luau with the one below.

def _build_global_map(tokens: List[Tuple[str, str]], locals_set: Set[str]) -> Dict[str, int]:
    """Identify global identifiers used as function calls and assign random indices."""
    globals_used = set()
    i = 0
    while i < len(tokens):
        kind, val = tokens[i]
        if kind == "identifier" and val not in locals_set:
            # look ahead for '(' (skipping whitespace/comments)
            j = i + 1
            while j < len(tokens) and tokens[j][0] in ("whitespace", "comment", "long_comment"):
                j += 1
            if j < len(tokens) and tokens[j][1] == "(":
                # make sure it's not a method call (like obj.method)
                if i > 0 and tokens[i-1][1] not in (".", ":"):
                    globals_used.add(val)
        i += 1
    # assign random indices
    mapping = {}
    for name in sorted(globals_used):
        mapping[name] = secrets.randbelow(0xFFFF) + 1
    return mapping

def _replace_globals(tokens: List[Tuple[str, str]], locals_set: Set[str],
                     global_map: Dict[str, int]) -> List[Tuple[str, str]]:
    """Replace global identifiers used as calls with _G_LOOKUP[index]."""
    out = []
    i = 0
    while i < len(tokens):
        kind, val = tokens[i]
        if kind == "identifier" and val in global_map:
            # check if it's a call (next non‑whitespace is '(')
            j = i + 1
            while j < len(tokens) and tokens[j][0] in ("whitespace", "comment", "long_comment"):
                j += 1
            if j < len(tokens) and tokens[j][1] == "(":
                # ensure it's not a method call (preceded by '.' or ':')
                if i > 0 and tokens[i-1][1] not in (".", ":"):
                    out.append(("identifier", "_G_LOOKUP"))
                    out.append(("symbol", "["))
                    out.append(("number", str(global_map[val])))
                    out.append(("symbol", "]"))
                    i += 1
                    continue
        out.append((kind, val))
        i += 1
    return out

def _flatten_control_flow(code: str, used: Set[str]) -> str:
    """Wrap the entire script in a while loop with a state variable (simple flattening)."""
    # This is a simplistic approach: we take the whole body and put it in a function,
    # then replace the function body with a while-loop dispatcher.
    # For real flattening we would need to parse basic blocks, but here we'll
    # just wrap the code in a loop that executes once with a state.
    # This still hides the linear flow somewhat.
    state_var = _fresh(used)
    new_body = []
    new_body.append(f"local {state_var}=0")
    new_body.append("while true do")
    new_body.append("if "+state_var+"==0 then")
    # insert the original code indented
    for line in code.splitlines():
        new_body.append("    "+line)
    new_body.append(f"    {state_var}=1")
    new_body.append("elseif "+state_var+"==1 then")
    new_body.append("    break")
    new_body.append("end")
    new_body.append("end")
    return "\n".join(new_body)

def _insert_junk(code: str, used: Set[str]) -> str:
    """Insert random junk statements and opaque predicates between lines."""
    lines = code.splitlines()
    new_lines = []
    for line in lines:
        new_lines.append(line)
        # insert junk after about 30% of lines
        if secrets.randbelow(100) < 30:
            dummy = _fresh(used)
            junk = f"{dummy}=({_mba(secrets.randbelow(0xFFFF))})"
            new_lines.append(junk)
        # insert opaque predicate if statement
        if secrets.randbelow(100) < 20:
            pred = _opaque()
            if secrets.randbelow(2) == 0:
                new_lines.append(f"if {pred} then end")
            else:
                new_lines.append(f"if not ({pred}) then end")
    return "\n".join(new_lines)

def _generate_lookup_table(global_map: Dict[str, int]) -> str:
    """Create a table that maps indices to the actual global functions."""
    parts = []
    for name, idx in global_map.items():
        # we need to assign the global function to the table at that index
        parts.append(f"_[{idx}]={name}")
    return "local _G_LOOKUP = {}\n" + "\n".join(parts) + "\n"

def obfuscate_luau(source: str) -> str:
    used: Set[str] = {v for k, v in _tokens(source) if k == "identifier"}

    # 1. rename locals
    parsed = _rename_locals(list(_tokens(source)), used)
    # rebuild set of local names (after rename)
    locals_set = set()
    # (we can collect locals from parsed tokens, but we already have used after rename? We'll just reuse the same logic)
    # For simplicity, we'll recompute locals from the parsed tokens
    # but the rename function already added renamed names to used, so we can use that.
    # However, we need to know which identifiers are local; we can use the same logic as _rename_locals
    # but we already have the original source's locals. Let's collect them from the original source before rename.
    # For the global replacement, we need to know which identifiers are local in the original source.
    # We'll run the local detection again on the parsed tokens (they are already renamed).
    # But we need the original local names to map to renamed ones? Actually, after rename, all locals have new names,
    # and global identifiers remain unchanged. So we can detect locals from the parsed tokens by looking at declarations.
    # We'll write a simple function to collect local names from parsed tokens.
    local_names = set()
    sig = [(i, k, v) for i, (k, v) in enumerate(parsed) if k not in ("whitespace", "comment", "long_comment")]
    depth = 0
    for i, (_, kind, val) in enumerate(sig):
        if val == "local" and depth == 0:
            nxt = i + 1
            if nxt < len(sig) and sig[nxt][2] == "function":
                nxt += 1
                if nxt < len(sig) and sig[nxt][1] == "identifier":
                    local_names.add(sig[nxt][2])
            else:
                expect = True
                while nxt < len(sig):
                    _, nk, nv = sig[nxt]
                    if nv in ("=", ";"):
                        break
                    if expect and nk == "identifier":
                        local_names.add(nv)
                        expect = False
                    elif nv == ",":
                        expect = True
                    nxt += 1
        if val in ("function", "do", "then", "repeat"):
            depth += 1
        elif val in ("end", "until"):
            depth = max(0, depth - 1)
    # now we have local names (renamed)

    # 2. build global map from original tokens before renaming? Actually we need to use the original identifiers,
    #    but after renaming, globals remain same. So we can use parsed tokens and locals_set.
    global_map = _build_global_map(parsed, local_names)
    # 3. replace globals with lookup
    parsed = _replace_globals(parsed, local_names, global_map)

    # 4. process strings and numbers (same as before)
    records = []
    out_tok = []
    for kind, val in parsed:
        if kind in ("comment", "long_comment"):
            continue
        if kind == "whitespace":
            continue
        if kind in ("string", "long_string"):
            rec = _encrypt(val)
            if rec is None:
                out_tok.append(val)
            else:
                records.append(rec)
                out_tok.append(f"__R{len(records)-1}__")
            continue
        if kind == "number":
            out_tok.append(_mask_num(val))
            continue
        out_tok.append(val)

    # shuffle and pack strings
    pool_ord = list(range(len(records)))
    secrets.SystemRandom().shuffle(pool_ord)
    o2n = {old: new for new, old in enumerate(pool_ord)}
    srecs = [records[o] for o in pool_ord]

    # build opcode stream (same)
    slots = list(range(16))
    secrets.SystemRandom().shuffle(slots)
    remap = slots[:2]  # OP_LOAD, OP_JUNK
    stream = []
    for old_idx in range(len(records)):
        for _ in range(secrets.randbelow(3)):
            stream.append(_pack(1, secrets.randbelow(0xFFFF), remap))  # junk
        stream.append(_pack(0, o2n[old_idx], remap))  # load
    for _ in range(secrets.randbelow(4)):
        stream.append(_pack(1, secrets.randbelow(0xFFFF), remap))

    stream_chk = 0
    for p in stream:
        stream_chk ^= p
    stream_chk &= 0xFFFFFFFF

    # pool integrity
    guard = 0
    a_rows, b_rows, c_rows, meta_rows = [], [], [], []
    for idx, rec in enumerate(srecs, 1):
        a_rows.append("{" + ",".join(map(str, rec["a"])) + "}")
        b_rows.append("{" + ",".join(map(str, rec["b"])) + "}")
        c_rows.append("{" + ",".join(map(str, rec["c"])) + "}")
        meta_rows.append("{" + ",".join(map(str, [rec["n"], rec["seed"], rec["add"], rec["step"], rec["blk"], rec["rot"], rec["rev"], rec["chk"]])) + "}")
        guard = (guard + idx * 31 + rec["seed"] * 19 + rec["add"] * 13 + rec["step"] * 7 + rec["blk"] * 23 + rec["rot"] * 5 + rec["rev"] * 3 + rec["chk"]) % 0x100000000

    final_guard = (guard ^ stream_chk) & 0xFFFFFFFF

    # replace placeholders with decode calls
    n_dec = _fresh(used)
    for i, t in enumerate(out_tok):
        if t.startswith("__R") and t.endswith("__"):
            oi = int(t[3:-2])
            out_tok[i] = f"{n_dec}({o2n[oi]})"

    body = _compact(out_tok).strip()

    # 5. control-flow flattening on the body (simple)
    body = _flatten_control_flow(body, used)

    # 6. inject junk code
    body = _insert_junk(body, used)

    # 7. generate the runtime wrapper (with lookup table for globals)
    N = lambda: _fresh(used, confuse=True)
    n_bxor, n_bor, n_band, n_ls, n_rs = N(), N(), N(), N(), N()
    n_char, n_cat, n_type, n_floor, n_trap = N(), N(), N(), N(), N()
    n_tA, n_tB, n_tC, n_meta, n_cache = N(), N(), N(), N(), N()
    n_stream, n_hnd, n_disp, n_vms = N(), N(), N(), N()
    n_gv, n_cv = N(), N()
    n_i, n_j, n_r, n_out, n_v, n_pf = N(), N(), N(), N(), N(), N()
    n_op, n_opr, n_pc, n_ins, n_val, n_tmp = N(), N(), N(), N(), N(), N()
    dec = [N() for _ in range(5)]

    load_wire = _mba(remap[0])
    junk_wire = _mba(remap[1])
    jw_raw = remap[1]

    # lookup table code
    lookup_code = _generate_lookup_table(global_map) if global_map else ""

    decoy_init = "\n".join(f"local {d}={_mba(secrets.randbelow(255))}" for d in dec)
    decoy_use = f"if false then {dec[0]}={dec[1]}+{dec[2]} {dec[3]}={dec[4]}-{dec[0]} end"

    rt = f"""\
local {n_bxor}=bit32.bxor
local {n_bor}=bit32.bor
local {n_band}=bit32.band
local {n_ls}=bit32.lshift
local {n_rs}=bit32.rshift
local {n_char}=string.char
local {n_cat}=table.concat
local {n_type}=type
local {n_floor}=math.floor
local {n_trap}=function()error("",0)end
{decoy_init}
{decoy_use}
if not({n_type}(bit32)=={n_type}({{}}))then {n_trap}()end
if not {_opaque()} then {n_trap}()end
local {n_tA}={{{",".join(a_rows)}}}
local {n_tB}={{{",".join(b_rows)}}}
local {n_tC}={{{",".join(c_rows)}}}
local {n_meta}={{{",".join(meta_rows)}}}
local {n_cache}={{}}
local {n_gv}=0
for {n_i}=1,#{n_meta} do
local {n_r}={n_meta}[{n_i}]
{n_gv}=({n_gv}+{n_i}*31+{n_r}[2]*19+{n_r}[3]*13+{n_r}[4]*7+{n_r}[5]*23+{n_r}[6]*5+{n_r}[7]*3+{n_r}[8])%4294967296
end
local {n_stream}={{{",".join(map(str, stream))}}}
local {n_cv}=0
for {n_i}=1,#{n_stream} do {n_cv}={n_bxor}({n_cv},{n_stream}[{n_i}])end
{n_cv}={n_band}({n_cv},4294967295)
if {n_bxor}({n_gv}%4294967296,{n_cv})~={_mba(final_guard)} then {n_trap}()end
if not {_opaque()} then {n_trap}()end
local {n_dec}=function({n_i})
if {n_cache}[{n_i}]~=nil then return {n_cache}[{n_i}]end
local {n_r}={n_meta}[{n_i}+1]
if {n_r}==nil then {n_trap}()end
local {n_out}={{}}
local {n_cv}=2166136261
local function {n_pf}(s,pos)
local sl=(pos-1)%3
local si={n_floor}((pos-1)/3)+1
if sl==0 then return {n_tA}[s][si]
elseif sl==1 then return {n_tB}[s][si]
else return {n_tC}[s][si]end
end
for {n_j}=1,{n_r}[1] do
local src={n_j}
if {n_r}[7]~=0 then src={n_r}[1]-{n_j}+1 end
local {n_v}={n_pf}({n_i}+1,src)
{n_v}={n_bxor}({n_v},{n_r}[5])
{n_v}={n_bor}({n_rs}({n_v},{n_r}[6]),{n_ls}({n_v},8-{n_r}[6]))
{n_v}={n_band}({n_v},255)
{n_v}={n_bxor}({n_v},({n_r}[2]+({n_j}-1)*{n_r}[4]+{n_r}[3])%256)
if {n_v}<0 then {n_v}={n_v}+256 end
{n_cv}={n_band}(({n_bxor}({n_cv},{n_v})*16777619),4294967295)
{n_out}[{n_j}]={n_char}({n_v})
end
if {n_cv}~={n_r}[8] then {n_trap}()end
local {n_val}={n_cat}({n_out})
{n_cache}[{n_i}]={n_val}
return {n_val}
end
local {n_hnd}={{}}
for {n_i}=0,15 do {n_hnd}[{n_i}]=function()return nil end end
{n_hnd}[{load_wire}]=function({n_opr})return {n_dec}({n_opr})end
{n_hnd}[{junk_wire}]=function()return nil end
if {n_hnd}[{_mba(jw_raw)}]()~=nil then {n_trap}()end
if not {_opaque()} then {n_trap}()end
local {n_vms}={{pc=1,last=nil}}
local {n_disp}=function()
local {n_pc}={n_vms}.pc
while {n_pc}<=#{{n_stream}} do
local {n_ins}={n_stream}[{n_pc}]
local {n_op}={n_ins}%16
local {n_opr}={n_floor}({n_ins}/64)%65536
local {n_tmp}={n_hnd}[{n_op}]
if {n_tmp} then
local {n_val}={n_tmp}({n_opr})
if {n_val}~=nil then {n_vms}.last={n_val} end
end
{n_pc}={n_pc}+1
end
{n_vms}.pc={n_pc}
end
{n_disp}()
{lookup_code}
"""

    header = ["-- obfuscated by buterfuscate v4", rt]
    return "\n".join(header) + "\n" + body + "\n"
