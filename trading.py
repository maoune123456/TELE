#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()
import os
import re
import logging
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from tradingview_ta import TA_Handler, Interval
from keep_alive import keep_alive

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Alerts storage
alerts = {}

# Price check interval (in seconds)
CHECK_INTERVAL = 29

# Set up intents
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix='/',
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        await self.tree.sync()
        logger.info('Slash commands synced.')
        check_prices.start()
        keep_alive_message.start()

bot = MyBot()

# Choices for screener and exchange
SCREENER_CHOICES = [
    app_commands.Choice(name='forex', value='forex'),
    app_commands.Choice(name='crypto', value='crypto'),
    app_commands.Choice(name='cfd', value='cfd'),
    app_commands.Choice(name='indices', value='indices'),
    app_commands.Choice(name='stocks', value='america'),
]
EXCHANGE_CHOICES = [
    app_commands.Choice(name='OANDA', value='OANDA'),
    app_commands.Choice(name='BINANCE', value='BINANCE'),
    app_commands.Choice(name='FX', value='FX'),
    app_commands.Choice(name='PEPPERSTONE', value='PEPPERSTONE'),
    app_commands.Choice(name='FOREXCOM', value='FOREXCOM'),
    app_commands.Choice(name='TVC', value='TVC'),
    app_commands.Choice(name='CAPITALCOM', value='CAPITALCOM'),
    app_commands.Choice(name='BITFINEX', value='BITFINEX'),
    app_commands.Choice(name='KRAKEN', value='KRAKEN'),
    app_commands.Choice(name='COINBASE', value='COINBASE'),
    app_commands.Choice(name='BITSTAMP', value='BITSTAMP'),
    app_commands.Choice(name='CRYPTOCAP', value='CRYPTOCAP'),
    app_commands.Choice(name='MEXC', value='MEXC'),
]

@tasks.loop(seconds=CHECK_INTERVAL)
async def check_prices():
    for guild_id, guild_alerts in list(alerts.items()):
        for alert_obj in guild_alerts.copy():
            try:
                # Run blocking get_analysis in thread to avoid blocking event loop
                analysis = await asyncio.to_thread(lambda: TA_Handler(
                    symbol=alert_obj['symbol'],
                    screener=alert_obj['screener'],
                    exchange=alert_obj['exchange'],
                    interval=Interval.INTERVAL_5_MINUTES
                ).get_analysis())
                indicators = analysis.indicators
                high_price = float(indicators.get('high', 0))
                low_price = float(indicators.get('low', 0))

                if low_price <= alert_obj['target_price'] <= high_price:
                    channel = bot.get_channel(alert_obj['channel_id'])
                    if channel:
                        mentions = ' '.join(f"<@{uid}>" for uid in alert_obj.get('mention_user_ids', []))
                        await channel.send(
                            f"Alert triggered for symbol {alert_obj['symbol']} at target price {alert_obj['target_price']}. {mentions}".strip()
                        )
                    guild_alerts.remove(alert_obj)
                    logger.info(f"Alert triggered for {alert_obj['symbol']} at {alert_obj['target_price']} in guild {guild_id}")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout fetching data for {alert_obj['symbol']} ({alert_obj['screener']}, {alert_obj['exchange']})")
            except Exception as e:
                logger.error(f"Error fetching data for {alert_obj['symbol']} ({alert_obj['screener']}, {alert_obj['exchange']}): {e}")

@tasks.loop(seconds=60)
async def keep_alive_message():
    logger.info('أنا شغال ومفيش نوم!')

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    await restore_alerts_from_history()

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id and payload.guild_id in alerts:
        channel = bot.get_channel(payload.channel_id)
        if channel:
            try:
                message = await channel.fetch_message(payload.message_id)
            except Exception as e:
                logger.error(f"Error fetching message for reaction: {e}")
                return
            if message.author.id == bot.user.id:
                for alert_obj in alerts[payload.guild_id]:
                    if alert_obj.get('message_id') == payload.message_id:
                        if payload.user_id != bot.user.id:
                            alert_obj.setdefault('mention_user_ids', set()).add(payload.user_id)
                        break

async def restore_alerts_from_history():
    for guild in bot.guilds:
        guild_alerts = alerts.setdefault(guild.id, [])
        for channel in guild.text_channels:
            perms = channel.permissions_for(guild.me or guild.get_member(bot.user.id))
            if not perms.read_message_history:
                continue
            messages = []
            try:
                async for msg in channel.history(limit=50):
                    messages.append(msg)
            except Exception as e:
                logger.error(f"Error reading channel {channel.id}: {e}")
                continue
            for msg in messages:
                if msg.author.id == bot.user.id and msg.content.startswith('Alert set for symbol'):
                    pattern = (
                        r"Alert set for symbol\s+(.+?) at target price ([\d\.]+) using screener: (\w+) and exchange: (\w+).?(?: Note: (.*))?"
                    )
                    match = re.search(pattern, msg.content)
                    if not match:
                        continue
                    symbol_found = match.group(1).upper()
                    target_price = float(match.group(2))
                    screener_found = match.group(3).lower()
                    exchange_found = match.group(4).upper()
                    note = match.group(5) or ''

                    if any(a['symbol'] == symbol_found and a['target_price'] == target_price and a['channel_id'] == channel.id for a in guild_alerts):
                        continue

                    mention_user_ids = set()
                    try:
                        fresh = await channel.fetch_message(msg.id)
                        for reaction in fresh.reactions:
                            async for user in reaction.users():
                                if user.id != bot.user.id:
                                    mention_user_ids.add(user.id)
                    except Exception:
                        pass

                    guild_alerts.append({
                        'symbol': symbol_found,
                        'screener': screener_found,
                        'exchange': exchange_found,
                        'target_price': target_price,
                        'channel_id': channel.id,
                        'message_id': msg.id,
                        'note': note,
                        'mention_user_ids': mention_user_ids
                    })
                    logger.info(f"Restored alert for {symbol_found} at {target_price} in channel {channel.id}")

@bot.tree.command(name='alert', description='Set an alert for a specific symbol')
@app_commands.describe(
    screener='Choose the screener type',
    exchange='Choose the exchange/platform',
    symbol='Symbol name',
    target_price='The target price for the alert',
    note='An additional note (optional)'
)
@app_commands.choices(screener=SCREENER_CHOICES, exchange=EXCHANGE_CHOICES)
async def alert(interaction: discord.Interaction, screener: str, exchange: str, symbol: str, target_price: float, note: str = ''):
    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    alerts.setdefault(guild_id, [])
    content = (
        f"Alert set for symbol {symbol.upper()} at target price {target_price} using screener: {screener} and exchange: {exchange}."
    )
    if note:
        content += f" Note: {note}"
    try:
        await interaction.response.send_message(content)
        sent_msg = await interaction.original_response()
    except discord.errors.NotFound:
        sent_msg = await interaction.followup.send(content, wait=True)
    alerts[guild_id].append({
        'symbol': symbol.upper(),
        'screener': screener.lower(),
        'exchange': exchange.upper(),
        'target_price': target_price,
        'channel_id': channel_id,
        'message_id': sent_msg.id,
        'note': note,
        'mention_user_ids': set()
    })
    logger.info(f"New alert added: {symbol.upper()} at {target_price} (screener={screener}, exchange={exchange})")

@bot.tree.command(name='cancel', description='Cancel a previously set alert')
@app_commands.describe(
    symbol='Symbol name',
    target_price='Target price of the alert to cancel'
)
async def cancel(interaction: discord.Interaction, symbol: str, target_price: float):
    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    guild_alerts = alerts.get(guild_id, [])
    if not guild_alerts:
        await interaction.response.send_message('ليس هناك تنبيهات لإلغاءها.', ephemeral=True)
        return
    before = len(guild_alerts)
    alerts[guild_id] = [
        a for a in guild_alerts
        if not (a['symbol'] == symbol.upper() and a['target_price'] == target_price and a['channel_id'] == channel_id)
    ]
    after = len(alerts[guild_id])
    if before == after:
        await interaction.response.send_message(f"لم أجد تنبيه للرمز {symbol.upper()} عند السعر {target_price}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"تم إلغاء التنبيه للرمز {symbol.upper()} عند السعر {target_price}.", ephemeral=True)

# Start the keep_alive server
keep_alive()
# Run the bot
bot.run(os.getenv('DISCORD_TOKEN'))
