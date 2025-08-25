import re
import discord
from discord.ext import commands
import yt_dlp
import urllib
import asyncio
import threading
import os
import shutil
import sys
import subprocess as sp
from dotenv import load_dotenv
import subprocess

import tracemalloc 

tracemalloc.start()

import logging

# (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)
logging.basicConfig(level=logging.DEBUG)

logging.debug("Κdebugging")
logging.info("(info)")
logging.warning("(warning)")
logging.error("(error)")
logging.critical("(critical)")


load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
PREFIX = os.getenv('BOT_PREFIX', '.')
YTDL_FORMAT = os.getenv('YTDL_FORMAT', 'worstaudio')
PRINT_STACK_TRACE = os.getenv('PRINT_STACK_TRACE', '1').lower() in ('true', 't', '1')
BOT_REPORT_COMMAND_NOT_FOUND = os.getenv('BOT_REPORT_COMMAND_NOT_FOUND', '1').lower() in ('true', 't', '1')
BOT_REPORT_DL_ERROR = os.getenv('BOT_REPORT_DL_ERROR', '0').lower() in ('true', 't', '1')
try:
    COLOR = int(os.getenv('BOT_COLOR', 'ff0000'), 16)
except ValueError:
    print('the BOT_COLOR in .env is not a valid hex color')
    print('using default color ff0000')
    COLOR = 0xff0000

bot = commands.Bot(command_prefix=PREFIX, intents=discord.Intents(voice_states=True, guilds=True, guild_messages=True, message_content=True))
queues = {} 

def main():
    if TOKEN is None:
        return ("no token provided. Please create a .env file containing the token.\n"
                "for more information view the README.md")
    try: bot.run(TOKEN)
    except discord.PrivilegedIntentsRequired as error:
        return error

@bot.command(name='play', aliases=['p'],help='play the song that you want to play')
async def play(ctx: commands.Context, *args):
    voice_state = ctx.author.voice
    if not await sense_checks(ctx, voice_state=voice_state):
        return

    query = ' '.join(args)
    # this is how it's determined if the url is valid (i.e. whether to search or not) under the hood of yt-dlp
    will_need_search = not urllib.parse.urlparse(query).scheme

    server_id = ctx.guild.id

    
    
    await ctx.send(f'looking for `{query}`...')
    with yt_dlp.YoutubeDL({'format': YTDL_FORMAT,
                            'source_address': '0.0.0.0', #Make all connections via IPv4 (line 312 of https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/options.py) ,ipv6 breaks it for some reason
                            'default_search': 'ytsearch',
                            'outtmpl': '%(id)s.%(ext)s',
                            'noplaylist': True,
                            'allow_playlist_files': False,
                            'merge_output_format': 'mp4',
                            'noplaylist': True,
                            'restrictfilenames': True,
                            'paths': {'home': f'./dl/{server_id}'}}) as ydl:
                            # to add from https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py on class YoutubeDL: line 167
                            
                            # 'progress_hooks': [lambda info, ctx=ctx: video_progress_hook(ctx, info)],
                            # 'match_filter': lambda info, incomplete, will_need_search=will_need_search, ctx=ctx: start_hook(ctx, info, incomplete, will_need_search),
        try:
            info = ydl.extract_info(query, download=False)
        except yt_dlp.utils.DownloadError as err:
            await notify_about_failure(ctx, err)
            return

        if 'entries' in info:
            info = info['entries'][0]
        # send link if it was a search, otherwise send title as sending link again would clutter chat with previews
        await ctx.send('downloading ' + (f'https://youtu.be/{info["id"]}' if will_need_search else f'`{info["title"]}`'))
        try:
            ydl.download([query])
        except yt_dlp.utils.DownloadError as err:
            await notify_about_failure(ctx, err)
            return
        
        path = f'./dl/{server_id}/{info["id"]}.{info["ext"]}'
        try:
            queues[server_id].append((path, info))
        except KeyError: # first in queue
            queues[server_id] = [(path, info)]
            try:
                connection = await voice_state.channel.connect()
            except discord.ClientException:
                connection = get_voice_client_from_channel_id(voice_state.channel.id)
            connection.play(discord.FFmpegOpusAudio(path), after=lambda error=None, connection=connection, server_id=server_id:
            bot.loop.create_task(after_track(error, connection, server_id)))

        try:
            connection.play(discord.FFmpegOpusAudio(queues[server_id][0][0]),
                            after=lambda error=None, connection=connection, server_id=server_id:
                            bot.loop.create_task(after_track(error, connection, server_id)))
        except IndexError:
            queues.pop(server_id)
            asyncio.run_coroutine_threadsafe(safe_disconnect(connection), bot.loop).result()


@bot.command(name='queue', aliases=['q'],help='Displays the current queue')
async def queue(ctx: commands.Context, *args):
    try: queue = queues[ctx.guild.id]
    except KeyError: queue = None
    if queue == None:
        await ctx.send('the bot isn\'t playing anything')
    else:
        title_str = lambda val: '‣ %s\n\n' % val[1] if val[0] == 0 else '**%2d:** %s\n' % val
        queue_str = ''.join(map(title_str, enumerate([i[1]["title"] for i in queue])))
        embedVar = discord.Embed(color=COLOR)
        embedVar.add_field(name='Now playing:', value=queue_str)
        await ctx.send(embed=embedVar)
    if not await sense_checks(ctx):
        return

@bot.command(name='skip', aliases=['s'], help='Skips the current track')
async def skip(ctx: commands.Context, *args):
    try: 
        queue_length = len(queues[ctx.guild.id])
    except KeyError: 
        queue_length = 0
        
    if queue_length <= 0:
        await ctx.send('the bot isn\'t playing anything')
        return
    
    try:
        result = await sense_checks(ctx)
        if not result:
            print("Sense checks failed!")
            await ctx.send("sense checks failed. You don't have permission to skip them.")
        else:
            print("Sense checks passed successfully!")
    except Exception as e:
            print(f"An error occurred: {e}")
            await ctx.send(f"An error occurred: {e}")


    try: n_skips = int(args[0])
    except IndexError:
        n_skips = 1
    except ValueError:
        if args[0] == 'all': n_skips = queue_length
        else: n_skips = 1
    if n_skips == 1:
        message = 'Next!!'
    elif n_skips < queue_length:
        message = f'skipping `{n_skips}` of `{queue_length}` tracks'
    else:
        message = 'All tracks out!!'
        n_skips = queue_length
    await ctx.send(message)

    voice_client = get_voice_client_from_channel_id(ctx.author.voice.channel.id)
    for _ in range(n_skips - 1):
            queues[ctx.guild.id].pop(0)
    voice_client.stop()

@bot.command(name='join',aliases=['j'] ,help='Tells the bot to join the voice channel')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("{} is not connected to a voice channel".format(ctx.message.author.name))
        return
    else:
        channel = ctx.message.author.voice.channel
    await channel.connect()

def get_voice_client_from_channel_id(channel_id: int):
    for voice_client in bot.voice_clients:
        if voice_client.channel.id == channel_id:
            return voice_client
        
async def after_track(error, connection, server_id):
    if error is not None:
        print(error)
    try:
        path = queues[server_id].pop(0)[0]
    except KeyError:
        return  
    if path not in [i[0] for i in queues[server_id]]:
        try:
            await asyncio.sleep(1)
            os.remove(path)
        except FileNotFoundError:
            pass
    try:
        connection.play(discord.FFmpegOpusAudio(queues[server_id][0][0]),
                        after=lambda error=None, connection=connection, server_id=server_id:
                        after_track(error, connection, server_id))
    except IndexError:
        queues.pop(server_id)
        asyncio.run_coroutine_threadsafe(safe_disconnect(connection), bot.loop).result()
        
async def safe_disconnect(connection):
    if not connection.is_playing():
        await connection.disconnect()
    
async def sense_checks(ctx: commands.Context, voice_state=None) -> bool:
    if voice_state is None: voice_state = ctx.author.voice 
    if voice_state is None:
        await ctx.send('join in a voice channel to use this command')
        return False

    if bot.user.id not in [member.id for member in ctx.author.voice.channel.members] and ctx.guild.id in queues.keys():
        await ctx.send('you have to be in the same voice channel as the bot to use this command')
        return False
    return True

@bot.event
async def on_voice_state_update(member: discord.User, before: discord.VoiceState, after: discord.VoiceState):
    if member != bot.user:
        return
    if before.channel is None and after.channel is not None: # joined vc
        return
    if before.channel is not None and after.channel is None: # disconnected from vc
        # clean up
        server_id = before.channel.guild.id
        try: queues.pop(server_id)
        except KeyError: pass
        try: shutil.rmtree(f'./dl/{server_id}/')
        except FileNotFoundError: pass

@bot.event
async def on_command_error(ctx: discord.ext.commands.Context, err: discord.ext.commands.CommandError):
    if isinstance(err, discord.ext.commands.errors.CommandNotFound):
        if BOT_REPORT_COMMAND_NOT_FOUND:
            await ctx.send("Type {}help ".format(PREFIX))
        return

@bot.event
async def on_ready():
    print(f'Hello there. ,{bot.user.name}')
    
    for guild in bot.guilds:
        for channel in guild.text_channels :
            if str(channel) == "general" :
                #await channel.send(file=discord.File('Image_1.jpg'))              στελνει μια εικονα
                return
    #print(f'logged in successfully as {bot.user.name}')
    
    
    
async def notify_about_failure(ctx: commands.Context, err: yt_dlp.utils.DownloadError):
    if BOT_REPORT_DL_ERROR:
        # remove shell colors for discord message
        sanitized = re.compile(r'\x1b[^m]*m').sub('', err.msg).strip()
        if sanitized[0:5].lower() == "error":
            # if message starts with error, strip it to avoid being redundant
            sanitized = sanitized[5:].strip(" :")
        await ctx.send('failed to download due to error: {}'.format(sanitized))
    else:
        await ctx.send('sorry, failed to download this video')
    return

if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemError as error:
        if PRINT_STACK_TRACE:
            raise
        else:
            print(error)
