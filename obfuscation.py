from __future__ import annotations

import asyncio
import io
import re
import secrets
from pathlib import Path
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands


MAX_SOURCE_BYTES = 750_000
ATTRIBUTION = "-- obfuscated by buterfuscate"

_TOKEN_RE = re.compile(
    r"""
    (?P<long_comment>--\[(?:=*)\[.*?\](?:=*)\])
    |(?P<comment>--[^\r\n]*)
    |(?P<whitespace>\s+)
    |(?P<long_string>\[(?:=*)\[.*?\](?:=*)\])
    |(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
    |(?P<number>0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)
    |(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<symbol>.)
    """,
    re.DOTALL | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Identifier generation
# ---------------------------------------------------------------------------

# Two visually-confusable pools: l/I/1 look-alikes + O/0 look-alikes.
# Mixing them makes identifiers hard to read or transcribe.
_CONFUSE_POOL = "lIlIlIlIlIOo0OoO0Oo"
_ALPHA_POOL = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


def _fresh_identifier(used: set[str], *, confuse: bool = False) -> str:
    pool = _CONFUSE_POOL if confuse else _ALPHA_POOL
    while True:
        length = secrets.randbelow(8) + 10  # 10-17 chars
        body = "".join(secrets.choice(pool) for _ in range(length))
        # Must start with a letter or underscore
        first = secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
        value = first + body
        if value not in used:
            used.add(value)
            return value


# ---------------------------------------------------------------------------
# Tokeniser helpers
# ---------------------------------------------------------------------------

def _tokens(source: str) -> Iterable[tuple[str, str]]:
    position = 0
    while position < len(source):
        match = _TOKEN_RE.match(source, position)
        if match is None:
            yield "symbol", source[position]
            position += 1
            continue
        position = match.end()
        yield match.lastgroup or "symbol", match.group(0)


def _literal_bytes(value: str) -> list[int] | None:
    if value.startswith("["):
        match = re.match(r"^\[(=*)\[(.*)\]\1\]$", value, re.DOTALL)
        if not match:
            return None
        return list(match.group(2).encode("utf-8"))

    if len(value) < 2 or value[0] not in {"'", '"'} or value[-1] != value[0]:
        return None

    body = value[1:-1]
    result: list[int] = []
    index = 0
    escapes = {
        "a": 7, "b": 8, "f": 12, "n": 10, "r": 13,
        "t": 9, "v": 11, "\\": 92, "'": 39, '"': 34,
    }
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(body):
            return None
        escaped = body[index]
        if escaped in escapes:
            result.append(escapes[escaped])
            index += 1
            continue
        if escaped == "x" and index + 2 < len(body):
            digits = body[index + 1: index + 3]
            if re.fullmatch(r"[0-9a-fA-F]{2}", digits):
                result.append(int(digits, 16))
                index += 3
                continue
        if escaped.isdigit():
            digits = escaped
            cursor = index + 1
            while cursor < len(body) and len(digits) < 3 and body[cursor].isdigit():
                digits += body[cursor]
                cursor += 1
            number = int(digits, 10)
            if number > 255:
                return None
            result.append(number)
            index = cursor
            continue
        if escaped in {"\n", "\r"}:
            if escaped == "\r" and index + 1 < len(body) and body[index + 1] == "\n":
                index += 1
            result.append(10)
            index += 1
            continue
        return None
    return result


# ---------------------------------------------------------------------------
# Top-level local renaming
# ---------------------------------------------------------------------------

def _rename_top_level_locals(
    parsed: list[tuple[str, str]],
    used: set[str],
) -> list[tuple[str, str]]:
    significant = [
        (i, k, v)
        for i, (k, v) in enumerate(parsed)
        if k not in {"whitespace", "comment", "long_comment"}
    ]
    declarations: set[str] = set()
    block_depth = 0
    for idx, (_, kind, value) in enumerate(significant):
        if value == "local" and block_depth == 0:
            nxt = idx + 1
            if nxt < len(significant) and significant[nxt][2] == "function":
                nxt += 1
                if nxt < len(significant) and significant[nxt][1] == "identifier":
                    declarations.add(significant[nxt][2])
            else:
                expect_name = True
                while nxt < len(significant):
                    _, nk, nv = significant[nxt]
                    if nv in {"=", ";"}:
                        break
                    if expect_name and nk == "identifier":
                        declarations.add(nv)
                        expect_name = False
                    elif nv == ",":
                        expect_name = True
                    nxt += 1
        if value in {"function", "do", "then", "repeat"}:
            block_depth += 1
        elif value in {"end", "until"}:
            block_depth = max(0, block_depth - 1)

    if not declarations:
        return parsed

    rename_map: dict[str, str] = {}
    for name in sorted(declarations):
        rename_map[name] = _fresh_identifier(used, confuse=True)

    prev_sig: list[str] = []
    prev = ""
    for kind, value in parsed:
        prev_sig.append(prev)
        if kind not in {"whitespace", "comment", "long_comment"}:
            prev = value
    next_sig: list[str] = [""] * len(parsed)
    following = ""
    for i in range(len(parsed) - 1, -1, -1):
        next_sig[i] = following
        k, v = parsed[i]
        if k not in {"whitespace", "comment", "long_comment"}:
            following = v

    renamed: list[tuple[str, str]] = []
    brace_depth = 0
    for i, (kind, value) in enumerate(parsed):
        if kind == "symbol":
            if value == "{":
                brace_depth += 1
            elif value == "}":
                brace_depth = max(0, brace_depth - 1)
        if kind != "identifier" or value not in rename_map:
            renamed.append((kind, value))
            continue
        is_field = (
            prev_sig[i] in {".", ":"}
            or (brace_depth > 0 and next_sig[i] == "=")
        )
        renamed.append((kind, value if is_field else rename_map[value]))
    return renamed


# ---------------------------------------------------------------------------
# Number masking  (now uses randomised arithmetic expressions)
# ---------------------------------------------------------------------------

def _mask_number(value: str, idx: int) -> str:
    if not re.fullmatch(r"\d+", value) or len(value) > 7:
        return value
    number = int(value)
    if number > 10_000_000:
        return value

    style = secrets.randbelow(4)
    if style == 0:
        # (n+k-k)
        k = secrets.randbelow(200) + 5
        return f"({number + k}-{k})"
    elif style == 1:
        # (n*k//k)  – integer-safe
        k = secrets.randbelow(6) + 2
        return f"(({number * k})//{k})"
    elif style == 2:
        # bit32.bxor(n^mask, mask)  — only for small values
        if number < 65536:
            mask = secrets.randbelow(0xFFFF) + 1
            return f"bit32.bxor({number ^ mask},{mask})"
        k = secrets.randbelow(200) + 5
        return f"({number + k}-{k})"
    else:
        # ((n + a) - a)  with different a
        a = (idx * 53 + secrets.randbelow(127)) % 127 + 1
        return f"(({number + a})-{a})"


# ---------------------------------------------------------------------------
# Token separator
# ---------------------------------------------------------------------------

def _needs_separator(prev: str, cur: str) -> bool:
    if not prev or not cur:
        return False
    if (prev[-1].isalnum() or prev[-1] == "_") and (cur[0].isalnum() or cur[0] == "_"):
        return True
    return prev.endswith("-") and cur.startswith("-")


# ---------------------------------------------------------------------------
# Multi-stage string encryption
# ---------------------------------------------------------------------------
# Stage 1: per-byte XOR with a rolling key derived from position + seed
# Stage 2: byte permutation (split into 3 interleaved sub-arrays)
# Stage 3: outer XOR with a single block key
# Each string record carries its own independent parameter set.

def _encode_string(value: str) -> dict | None:
    plain = _literal_bytes(value)
    if plain is None:
        return None
    if len(plain) == 0:
        # empty string – encode trivially
        return {
            "a": [], "b": [], "c": [],
            "seed": 0, "add": 0, "step": 0,
            "blk": 0, "rot": 0, "rev": 0,
            "n": 0, "chk": 0,
        }

    n = len(plain)
    seed = secrets.randbelow(251) + 1
    add = secrets.randbelow(251)
    step = secrets.randbelow(31) + 1
    blk = secrets.randbelow(251) + 1   # outer XOR key
    rot = secrets.randbelow(7) + 1     # bit-rotate amount
    rev = secrets.randbelow(2)         # reverse before split

    # Stage 1: rolling XOR
    stage1: list[int] = []
    for i, byte in enumerate(plain):
        k = (seed + i * step + add) % 256
        stage1.append(byte ^ k)

    # Stage 2: bit-rotate each byte
    stage2 = [((b << rot) | (b >> (8 - rot))) & 0xFF for b in stage1]

    # Stage 3: outer block XOR
    stage3 = [b ^ blk for b in stage2]

    # Optionally reverse
    if rev:
        stage3 = stage3[::-1]

    # Split into 3 interleaved sub-arrays
    a_part = stage3[0::3]
    b_part = stage3[1::3]
    c_part = stage3[2::3]

    # Integrity checksum over plaintext (fnv-like)
    chk = 2166136261
    for byte in plain:
        chk = ((chk ^ byte) * 16777619) & 0xFFFFFFFF

    return {
        "a": a_part, "b": b_part, "c": c_part,
        "seed": seed, "add": add, "step": step,
        "blk": blk, "rot": rot, "rev": rev,
        "n": n, "chk": chk,
    }


# ---------------------------------------------------------------------------
# Control-flow wrapper  (opaque predicate)
# ---------------------------------------------------------------------------

def _opaque_true(names: dict[str, str]) -> str:
    """Emit a Lua expression that always evaluates to true but looks non-trivial."""
    variant = secrets.randbelow(3)
    bxor = names["bxor"]
    band = names["band"]
    if variant == 0:
        x = secrets.randbelow(200) + 10
        return f"({bxor}({x},{x})==0)"
    elif variant == 1:
        x = secrets.randbelow(100) + 5
        return f"({band}({x},0)==0)"
    else:
        x = secrets.randbelow(50) + 3
        y = x * 2
        return f"(({y})//{x}==2)"


# ---------------------------------------------------------------------------
# Main obfuscation entry point
# ---------------------------------------------------------------------------

def obfuscate_luau(source: str) -> str:
    used_identifiers: set[str] = {
        v for k, v in _tokens(source) if k == "identifier"
    }

    parsed = _rename_top_level_locals(list(_tokens(source)), used_identifiers)

    # Allocate runtime names – use confusing identifiers
    name_keys = [
        "bxor", "bor", "band", "lshift", "rshift",
        "char", "concat", "type_fn", "trap",
        "tbl_a", "tbl_b", "tbl_c", "meta",
        "cache", "decode", "guard_var",
        "i_var", "j_var", "r_var", "out_var",
        "p_var", "v_var", "chk_var", "val_var",
    ]
    names: dict[str, str] = {}
    for key in name_keys:
        names[key] = _fresh_identifier(used_identifiers, confuse=True)

    # Also generate a handful of decoy local names that are declared but
    # used in dead/opaque code so they appear in symbol tables.
    decoy_names = [_fresh_identifier(used_identifiers, confuse=True) for _ in range(4)]

    directives: list[str] = []
    output_tokens: list[str] = []
    number_index = 0
    records: list[dict] = []

    for kind, value in parsed:
        if kind in {"comment", "long_comment"}:
            stripped = value.strip()
            if stripped.startswith("--!"):
                directives.append(stripped)
            continue
        if kind == "whitespace":
            continue
        if kind in {"string", "long_string"}:
            encoded = _encode_string(value)
            if encoded is None:
                output_tokens.append(value)
            else:
                records.append(encoded)
                output_tokens.append(f"__BF_REF_{len(records)}__")
            continue
        if kind == "number":
            output_tokens.append(_mask_number(value, number_index))
            number_index += 1
            continue
        output_tokens.append(value)

    # Shuffle record order so table positions don't match source order
    order = list(range(len(records)))
    secrets.SystemRandom().shuffle(order)
    record_ids = {old + 1: new + 1 for new, old in enumerate(order)}
    ref_prefix = "__BF_REF_"
    for i, token in enumerate(output_tokens):
        if token.startswith(ref_prefix) and token.endswith("__"):
            old_id = int(token[len(ref_prefix):-2])
            output_tokens[i] = f"{names['decode']}({record_ids[old_id]})"

    compact: list[str] = []
    for token in output_tokens:
        if compact and _needs_separator(compact[-1], token):
            compact.append(" ")
        compact.append(token)
    body = "".join(compact).strip()

    shuffled_records = [records[old] for old in order]

    # Build the three split payload tables and the metadata table
    a_rows: list[str] = []
    b_rows: list[str] = []
    c_rows: list[str] = []
    meta_rows: list[str] = []

    expected_guard: int = 0
    for idx, rec in enumerate(shuffled_records, start=1):
        a_rows.append("{" + ",".join(map(str, rec["a"])) + "}")
        b_rows.append("{" + ",".join(map(str, rec["b"])) + "}")
        c_rows.append("{" + ",".join(map(str, rec["c"])) + "}")
        meta_rows.append(
            "{"
            + ",".join(map(str, [
                rec["n"], rec["seed"], rec["add"], rec["step"],
                rec["blk"], rec["rot"], rec["rev"], rec["chk"],
            ]))
            + "}"
        )
        # Guard hash: mix all parameters
        expected_guard = (
            expected_guard
            + idx * 31
            + rec["seed"] * 19
            + rec["add"] * 13
            + rec["step"] * 7
            + rec["blk"] * 23
            + rec["rot"] * 5
            + rec["rev"] * 3
            + rec["chk"]
        ) % 0x100000000

    tbl_a_lit = "{" + ",".join(a_rows) + "}"
    tbl_b_lit = "{" + ",".join(b_rows) + "}"
    tbl_c_lit = "{" + ",".join(c_rows) + "}"
    meta_lit = "{" + ",".join(meta_rows) + "}"

    opaque = _opaque_true(names)

    # Decoy locals (dead code mixed into setup section)
    decoy_block = "\n".join(
        f"local {d}=0" for d in decoy_names
    )
    # Use two decoys in a dead branch so the Lua compiler keeps the symbols
    d0, d1 = decoy_names[0], decoy_names[1]
    d2, d3 = decoy_names[2], decoy_names[3]

    bxor = names["bxor"]
    bor = names["bor"]
    band = names["band"]
    lshift = names["lshift"]
    rshift = names["rshift"]
    char = names["char"]
    concat = names["concat"]
    type_fn = names["type_fn"]
    trap = names["trap"]
    tbl_a = names["tbl_a"]
    tbl_b = names["tbl_b"]
    tbl_c = names["tbl_c"]
    meta = names["meta"]
    cache = names["cache"]
    decode = names["decode"]
    guard_var = names["guard_var"]
    i_var = names["i_var"]
    j_var = names["j_var"]
    r_var = names["r_var"]
    out_var = names["out_var"]
    p_var = names["p_var"]
    v_var = names["v_var"]
    chk_var = names["chk_var"]
    val_var = names["val_var"]

    runtime = f"""\
local {bxor}=bit32.bxor
local {bor}=bit32.bor
local {band}=bit32.band
local {lshift}=bit32.lshift
local {rshift}=bit32.rshift
local {char}=string.char
local {concat}=table.concat
local {type_fn}=type
local {trap}=function()error()end
{decoy_block}
if {type_fn}(bit32)~={type_fn}({{}}) or {type_fn}(string)~={type_fn}({{}}) or {type_fn}(table)~={type_fn}({{}}) then {trap}()end
if not {opaque} then {trap}()end
if false then {d0}={d1}+{d2}-{d3} end
local {tbl_a}={tbl_a_lit}
local {tbl_b}={tbl_b_lit}
local {tbl_c}={tbl_c_lit}
local {meta}={meta_lit}
local {cache}={{}}
local {decode}=function({i_var})
if {cache}[{i_var}]~=nil then return {cache}[{i_var}]end
local {r_var}={meta}[{i_var}]
if {r_var}==nil then {trap}()end
local {out_var}={{}}
local {chk_var}=2166136261
local function {p_var}(s,pos)
local slot=(pos-1)%3
local sub_idx=((pos-1)//3)+1
if slot==0 then return {tbl_a}[s][sub_idx]
elseif slot==1 then return {tbl_b}[s][sub_idx]
else return {tbl_c}[s][sub_idx]end
end
for {j_var}=1,{r_var}[1] do
local src_pos={j_var}
if {r_var}[7]~=0 then src_pos={r_var}[1]-{j_var}+1 end
local {v_var}={p_var}({i_var},src_pos)
{v_var}={bxor}({v_var},{r_var}[5])
{v_var}={bor}({rshift}({v_var},{r_var}[6]),{lshift}({v_var},8-{r_var}[6]))
{v_var}={band}({v_var},255)
{v_var}={bxor}({v_var},({r_var}[2]+({j_var}-1)*{r_var}[4]+{r_var}[3])%256)
if {v_var}<0 then {v_var}={v_var}+256 end
{chk_var}={band}(({bxor}({chk_var},{v_var})*16777619),4294967295)
{out_var}[{j_var}]={char}({v_var})
end
if {chk_var}~={r_var}[8] then {trap}()end
local {val_var}={concat}({out_var})
{cache}[{i_var}]={val_var}
return {val_var}
end
local {guard_var}=0
for {i_var}=1,#{meta} do
local {r_var}={meta}[{i_var}]
{guard_var}=({guard_var}+{i_var}*31+{r_var}[2]*19+{r_var}[3]*13+{r_var}[4]*7+{r_var}[5]*23+{r_var}[6]*5+{r_var}[7]*3+{r_var}[8])%4294967296
end
if {guard_var}~={expected_guard} then {trap}()end"""

    header = [*dict.fromkeys(directives), ATTRIBUTION, runtime]
    return "\n".join(header) + "\n" + body + "\n"


# ---------------------------------------------------------------------------
# Filename helper
# ---------------------------------------------------------------------------

def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.txt"


# ---------------------------------------------------------------------------
# Discord cog  (unchanged interface)
# ---------------------------------------------------------------------------

class Obfuscation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="obf", description="Obfuscate a Luau .TXT file")
    @app_commands.describe(file="Attach the .TXT Luau source file to obfuscate")
    async def obf(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment | None = None,
    ):
        if file is None:
            await interaction.response.send_message(
                "Attach a `.TXT` file to continue. Use `/obf` and attach your Luau source file.",
                ephemeral=True,
            )
            return

        if not file.filename.lower().endswith(".txt"):
            await interaction.response.send_message(
                "Only `.TXT` files are supported.",
                ephemeral=True,
            )
            return

        if file.size and file.size > MAX_SOURCE_BYTES:
            await interaction.response.send_message(
                f"That file is too large. The limit is {MAX_SOURCE_BYTES // 1000} KB.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            source_bytes = await file.read()
            if len(source_bytes) > MAX_SOURCE_BYTES:
                raise ValueError("That file is too large.")
            source = source_bytes.decode("utf-8-sig")
            if not source.strip():
                raise ValueError("The uploaded file is empty.")

            obfuscated = await asyncio.to_thread(obfuscate_luau, source)
            output = discord.File(
                io.BytesIO(obfuscated.encode("utf-8")),
                filename=_output_name(file.filename),
            )
            await interaction.followup.send(
                content="-obfuscated by buterfuscate",
                file=output,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except UnicodeDecodeError:
            await interaction.followup.send(
                "I could not read that file as UTF-8 Luau source.",
                ephemeral=True,
            )
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
        except Exception:
            await interaction.followup.send(
                "The file could not be obfuscated. Check that it contains valid text and try again.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Obfuscation(bot))
