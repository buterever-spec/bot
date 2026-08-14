import discord
from discord.ext import commands
from discord import app_commands

COLORS = {
    "blurple":  {"label": "Blurple",  "emoji": "🔵", "value": discord.Color.blurple()},
    "green":    {"label": "Green",    "emoji": "🟢", "value": discord.Color.green()},
    "red":      {"label": "Red",      "emoji": "🔴", "value": discord.Color.red()},
    "gold":     {"label": "Gold",     "emoji": "🟡", "value": discord.Color.gold()},
    "orange":   {"label": "Orange",   "emoji": "🟠", "value": discord.Color.orange()},
    "purple":   {"label": "Purple",   "emoji": "🟣", "value": discord.Color.purple()},
    "white":    {"label": "White",    "emoji": "⚪", "value": discord.Color.light_grey()},
    "black":    {"label": "Dark",     "emoji": "⚫", "value": discord.Color.from_rgb(30, 30, 30)},
}


class ColorPickerView(discord.ui.View):
    def __init__(self, title: str, message: str, channel: discord.TextChannel):
        super().__init__(timeout=120)
        self._title = title
        self._message = message
        self._channel = channel

        options = [
            discord.SelectOption(label=c["label"], value=key, emoji=c["emoji"])
            for key, c in COLORS.items()
        ]
        sel = discord.ui.Select(placeholder="Pick an embed color...", options=options)
        sel.callback = self._color_chosen
        self.add_item(sel)

    async def _color_chosen(self, interaction: discord.Interaction):
        key = interaction.data["values"][0]
        color = COLORS[key]["value"]
        embed = discord.Embed(description=self._message, color=color)
        if self._title:
            embed.title = self._title
        await self._channel.send(embed=embed)
        await interaction.response.edit_message(content=f"✅ Announcement sent to {self._channel.mention}!", view=None)


class AnnounceModal(discord.ui.Modal, title="Create Announcement"):
    ann_title = discord.ui.TextInput(label="Title (optional)", placeholder="Leave blank for no title", required=False, max_length=256)
    message = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, placeholder="Type your announcement here...", required=True, max_length=2000)

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self._channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        title_val = self.ann_title.value.strip()
        msg_val = self.message.value.strip()
        view = ColorPickerView(title_val, msg_val, self._channel)
        await interaction.response.send_message("🎨 Pick a color for your announcement:", view=view, ephemeral=True)


class Announce(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ancc", description="Send an announcement embed to a channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(channel="Channel to send the announcement to")
    async def ancc(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        modal = AnnounceModal(target)
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(Announce(bot))