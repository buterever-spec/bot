import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Your Discord user ID
OWNER_ID = 1387446299938263113


# PREFIX COMMAND CHECK
@bot.check
async def global_owner_check(ctx):
    # Allow everyone to use Obfuscation commands
    if ctx.command and ctx.command.cog_name == "Obfuscation":
        return True

    # All other commands = owner only
    return ctx.author.id == OWNER_ID


# SLASH COMMAND CHECK
async def interaction_check(interaction: discord.Interaction):
    # Allow everyone to use Obfuscation slash commands
    if interaction.command:
        module = getattr(interaction.command, "module", "")

        if "obfuscation" in str(module).lower():
            return True

    # Everything else = owner only
    return interaction.user.id == OWNER_ID


bot.tree.interaction_check = interaction_check


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")

        # Hide slash commands from everyone except you
        guilds = bot.guilds

        for guild in guilds:
            try:
                await bot.tree.sync(guild=guild)

                print(f"Synced guild commands for {guild.name}")

            except Exception as e:
                print(f"Failed syncing {guild.name}: {e}")

    except Exception as e:
        print(f"Failed to sync commands: {e}")


async def load_cogs():
    await bot.load_extension("announce")
    await bot.load_extension("hercules_obfuscator")


async def main():
    async with bot:
        await load_cogs()

        token = os.environ.get("DISCORD_TOKEN") or os.environ.get("TOKEN")

        if not token:
            raise ValueError("DISCORD_TOKEN environment variable is not set.")

        await bot.start(token)


asyncio.run(main())
