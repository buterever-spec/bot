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
    """Decode common Lua literals without changing their byte representation."""
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
        "a": 7,
        "b": 8,
        "f": 12,
        "n": 10,
        "r": 13,
        "t": 9,
        "v": 11,
        "\\": 92,
        "'": 39,
        '"': 34,
    }
    while index < len(body):
        char = body[index]
        if char != "\\":
            encoded = char.encode("utf-8")
            result.extend(encoded)
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
            digits = body[index + 1 : index + 3]
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


def _rename_top_level_locals(
    parsed: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Rename top-level locals conservatively to avoid changing table fields."""
    significant = [
        (index, kind, value)
        for index, (kind, value) in enumerate(parsed)
        if kind not in {"whitespace", "comment", "long_comment"}
    ]
    declarations: set[str] = set()
    block_depth = 0
    index = 0
    while index < len(significant):
        token_index, kind, value = significant[index]
        if value == "local" and block_depth == 0:
            next_index = index + 1
            if (
                next_index < len(significant)
                and significant[next_index][2] == "function"
            ):
                next_index += 1
                if (
                    next_index < len(significant)
                    and significant[next_index][1] == "identifier"
                ):
                    declarations.add(significant[next_index][2])
            else:
                expect_name = True
                while next_index < len(significant):
                    _, next_kind, next_value = significant[next_index]
                    if next_value in {"=", ";"}:
                        break
                    if expect_name and next_kind == "identifier":
                        declarations.add(next_value)
                        expect_name = False
                    elif next_value == ",":
                        expect_name = True
                    next_index += 1

        if value in {"function", "do", "then", "repeat"}:
            block_depth += 1
        elif value in {"end", "until"}:
            block_depth = max(0, block_depth - 1)
        index += 1

    if not declarations:
        return parsed

    used = {value for kind, value in parsed if kind == "identifier"}
    rename_map: dict[str, str] = {}
    for name in sorted(declarations):
        candidate = _fresh_identifier(used)
        rename_map[name] = candidate

    previous_significant: list[str] = []
    previous = ""
    for kind, value in parsed:
        previous_significant.append(previous)
        if kind not in {"whitespace", "comment", "long_comment"}:
            previous = value
    next_significant: list[str] = [""] * len(parsed)
    following = ""
    for index in range(len(parsed) - 1, -1, -1):
        next_significant[index] = following
        kind, value = parsed[index]
        if kind not in {"whitespace", "comment", "long_comment"}:
            following = value

    renamed: list[tuple[str, str]] = []
    brace_depth = 0
    for token_index, (kind, value) in enumerate(parsed):
        if kind == "symbol":
            if value == "{":
                brace_depth += 1
            elif value == "}":
                brace_depth = max(0, brace_depth - 1)

        if kind != "identifier" or value not in rename_map:
            renamed.append((kind, value))
            continue

        is_field_name = (
            previous_significant[token_index] in {".", ":"}
            or (brace_depth > 0 and next_significant[token_index] == "=")
        )
        renamed.append((kind, value if is_field_name else rename_map[value]))
    return renamed


def _masked_integer(value: str, index: int) -> str:
    if not value.isdigit() or len(value) > 7:
        return value
    number = int(value)
    if number > 10_000_000:
        return value
    mask = ((index * 37) % 89) + 11
    return f"({number + mask}-{mask})"


def _needs_separator(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if (previous[-1].isalnum() or previous[-1] == "_") and (
        current[0].isalnum() or current[0] == "_"
    ):
        return True
    # Avoid turning `a - -b` into `a--b`, which Luau reads as a comment.
    return previous.endswith("-") and current.startswith("-")


def _fresh_identifier(used: set[str]) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
    while True:
        value = "__" + "".join(secrets.choice(alphabet) for _ in range(15))
        if value not in used:
            used.add(value)
            return value


def _encode_string(value: str) -> dict[str, int | list[int]] | None:
    plain = _literal_bytes(value)
    if plain is None:
        return None

    key = secrets.randbelow(255) + 1
    add = secrets.randbelow(251)
    step = secrets.randbelow(31) + 1
    rotate = secrets.randbelow(7) + 1
    reverse = secrets.randbelow(2)
    encoded: list[int] = []

    for index, byte in enumerate(plain):
        value_byte = (byte + add + index * step) % 256
        value_byte ^= key
        value_byte = (
            (value_byte << rotate) | (value_byte >> (8 - rotate))
        ) & 255
        encoded.append(value_byte)

    if reverse:
        encoded.reverse()

    checksum = sum((byte * 43) + ((index + 1) * 17) for index, byte in enumerate(plain))
    return {
        "data": encoded,
        "key": key,
        "add": add,
        "step": step,
        "rotate": rotate,
        "reverse": reverse,
        "checksum": checksum % 4294967296,
    }


def obfuscate_luau(source: str) -> str:
    parsed = _rename_top_level_locals(list(_tokens(source)))
    directives: list[str] = []
    output_tokens: list[str] = []
    number_index = 0
    used_identifiers = {
        value for kind, value in parsed if kind == "identifier"
    }
    names = {
        key: _fresh_identifier(used_identifiers)
        for key in (
            "bxor",
            "bor",
            "band",
            "lshift",
            "rshift",
            "char",
            "concat",
            "type",
            "left",
            "right",
            "meta",
            "cache",
            "decode",
            "trap",
            "handlers",
        )
    }
    records: list[dict[str, int | list[int]]] = []

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
            output_tokens.append(_masked_integer(value, number_index))
            number_index += 1
            continue
        output_tokens.append(value)

    compact: list[str] = []
    for token in output_tokens:
        if compact and _needs_separator(compact[-1], token):
            compact.append(" ")
        compact.append(token)

    order = list(range(len(records)))
    secrets.SystemRandom().shuffle(order)
    record_ids = {old + 1: new + 1 for new, old in enumerate(order)}
    ref_prefix = "__BF_REF_"
    for index, token in enumerate(output_tokens):
        if token.startswith(ref_prefix) and token.endswith("__"):
            old_id = int(token[len(ref_prefix) : -2])
            output_tokens[index] = f"{names['decode']}({record_ids[old_id]})"

    compact = []
    for token in output_tokens:
        if compact and _needs_separator(compact[-1], token):
            compact.append(" ")
        compact.append(token)
    body = "".join(compact).strip()

    shuffled_records = [records[old] for old in order]
    left_payload: list[str] = []
    right_payload: list[str] = []
    metadata: list[str] = []
    expected_guard = 0
    for index, record in enumerate(shuffled_records, start=1):
        data = list(record["data"])
        left_payload.append("{" + ",".join(map(str, data[0::2])) + "}")
        right_payload.append("{" + ",".join(map(str, data[1::2])) + "}")
        metadata.append(
            "{"
            + ",".join(
                (
                    str(len(data)),
                    str(record["key"]),
                    str(record["add"]),
                    str(record["step"]),
                    str(record["rotate"]),
                    str(record["reverse"]),
                    str(record["checksum"]),
                )
            )
            + "}"
        )
        expected_guard = (
            expected_guard
            + index * 31
            + int(record["key"]) * 17
            + int(record["add"]) * 13
            + int(record["step"]) * 7
            + int(record["rotate"]) * 5
            + int(record["reverse"]) * 3
            + int(record["checksum"])
        ) % 4294967296

    left_literal = "{" + ",".join(left_payload) + "}"
    right_literal = "{" + ",".join(right_payload) + "}"
    metadata_literal = "{" + ",".join(metadata) + "}"
    runtime = f"""
local {names['bxor']}=bit32.bxor
local {names['bor']}=bit32.bor
local {names['band']}=bit32.band
local {names['lshift']}=bit32.lshift
local {names['rshift']}=bit32.rshift
local {names['char']}=string.char
local {names['concat']}=table.concat
local {names['type']}=type
local {names['trap']}=function()error()end
 if {names['type']}(bit32)~={names['type']}({{}}) or {names['type']}(string)~={names['type']}({{}}) or {names['type']}(table)~={names['type']}({{}}) then {names['trap']}()end
local {names['left']}={left_literal}
local {names['right']}={right_literal}
local {names['meta']}={metadata_literal}
local {names['cache']}={{}}
local {names['handlers']}={{
[0]=function()return 0 end,
[1]=function(a)return a end,
[2]=function(a)return {names['bxor']}(a,a)end,
[3]=function()return nil end
}}
if false then {names['handlers']}[2](1)end
local {names['decode']}=function(i)
if {names['cache']}[i]~=nil then return {names['cache']}[i]end
local r={names['meta']}[i]
if r==nil then {names['trap']}()end
local out={{}}local check=0
for j=1,r[1] do
local p
local v
if j%2==1 then p=(j+1)/2 v={names['left']}[i][p]else p=j/2 v={names['right']}[i][p]end
if r[6]~=0 then
local q=r[1]-j+1
if q%2==1 then v={names['left']}[i][(q+1)/2]else v={names['right']}[i][q/2]end
end
v={names['bor']}({names['rshift']}(v,r[5]),{names['lshift']}(v,8-r[5]))
v={names['band']}(v,255)
v={names['bxor']}(v,r[2])
v=(v-r[3]-(j-1)*r[4])%256
if v<0 then v=v+256 end
check=(check+v*43+j*17)%4294967296
out[j]={names['char']}(v)
end
if check~=r[7] then {names['trap']}()end
local value={names['concat']}(out)
{names['cache']}[i]=value
return value
end
local guard=0
for i=1,#{names['meta']} do
local r={names['meta']}[i]
guard=(guard+i*31+r[2]*17+r[3]*13+r[4]*7+r[5]*5+r[6]*3+r[7])%4294967296
end
if guard~={expected_guard} then {names['trap']}()end
""".strip()
    header = [*dict.fromkeys(directives), ATTRIBUTION, runtime]
    return "\n".join(header) + "\n" + body + "\n"


def _output_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._")
    return f"{stem or 'script'}.obfuscated.txt"


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