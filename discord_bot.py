"""
每日股市資金流向 Discord Bot

使用方式：
  1. 複製 .env.example 為 .env，填入 DISCORD_TOKEN 與 DISCORD_CHANNEL_ID
  2. pip install -r requirements.txt
  3. python discord_bot.py

Bot 指令（需要管理員或 manage_messages 權限）：
  !foreign   手動觸發外資買賣超報告
  !trust     手動觸發投信買賣超報告
  !report    同時發送外資與投信報告
  !status    顯示 Bot 狀態與下次排程時間
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord.ext import commands
from dotenv import load_dotenv

from message_formatter import build_foreign_embed, build_trust_embed

# ── 設定 ───────────────────────────────────────────────────────────────────────

load_dotenv()

DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SEND_HOUR         = int(os.getenv("SEND_HOUR",   "16"))
SEND_MINUTE       = int(os.getenv("SEND_MINUTE", "30"))

TZ_TAIPEI = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Discord Bot ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
scheduler = AsyncIOScheduler(timezone="Asia/Taipei")


# ── 核心發送函式 ───────────────────────────────────────────────────────────────

async def send_daily_report() -> None:
    """發送外資與投信買賣超報告到指定頻道。"""
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        logger.error("找不到頻道 ID=%s，請確認 DISCORD_CHANNEL_ID 設定", DISCORD_CHANNEL_ID)
        return

    logger.info("開始發送每日報告...")

    try:
        foreign_embed = build_foreign_embed()
        await channel.send(embed=foreign_embed)
        logger.info("外資報告已發送")
    except Exception as e:
        logger.error("發送外資報告失敗: %s", e)
        await channel.send(f"⚠️ 外資報告抓取失敗：{e}")

    try:
        trust_embed = build_trust_embed()
        await channel.send(embed=trust_embed)
        logger.info("投信報告已發送")
    except Exception as e:
        logger.error("發送投信報告失敗: %s", e)
        await channel.send(f"⚠️ 投信報告抓取失敗：{e}")


# ── Bot 事件 ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    logger.info("Bot 已上線：%s (ID: %s)", bot.user, bot.user.id)

    # 每日排程
    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=SEND_HOUR, minute=SEND_MINUTE, timezone="Asia/Taipei"),
        id="daily_report",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()

    next_run = scheduler.get_job("daily_report").next_run_time
    logger.info(
        "每日報告排程：%02d:%02d（台灣時間），下次執行：%s",
        SEND_HOUR, SEND_MINUTE, next_run,
    )
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="台股資金流向",
        )
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ 您沒有執行此指令的權限。")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        logger.error("指令錯誤: %s", error)


# ── Bot 指令 ──────────────────────────────────────────────────────────────────

@bot.command(name="foreign", aliases=["外資"])
async def cmd_foreign(ctx: commands.Context) -> None:
    """手動觸發外資買賣超報告"""
    async with ctx.typing():
        try:
            embed = build_foreign_embed()
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error("cmd_foreign 失敗: %s", e)
            await ctx.send(f"⚠️ 抓取外資資料失敗：{e}")


@bot.command(name="trust", aliases=["投信"])
async def cmd_trust(ctx: commands.Context) -> None:
    """手動觸發投信買賣超報告"""
    async with ctx.typing():
        try:
            embed = build_trust_embed()
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error("cmd_trust 失敗: %s", e)
            await ctx.send(f"⚠️ 抓取投信資料失敗：{e}")


@bot.command(name="report", aliases=["報告"])
async def cmd_report(ctx: commands.Context) -> None:
    """同時發送外資與投信報告"""
    async with ctx.typing():
        await send_daily_report()


@bot.command(name="status", aliases=["狀態"])
async def cmd_status(ctx: commands.Context) -> None:
    """顯示 Bot 狀態與下次排程時間"""
    job = scheduler.get_job("daily_report")
    if job and job.next_run_time:
        # 轉換為台灣時間
        next_run = job.next_run_time.astimezone(TZ_TAIPEI)
        next_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
    else:
        next_str = "排程未啟動"

    now_tw = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    embed = discord.Embed(title="🤖 Bot 狀態", color=discord.Color.green())
    embed.add_field(name="目前時間（台灣）", value=now_tw, inline=False)
    embed.add_field(
        name="每日發送時間",
        value=f"{SEND_HOUR:02d}:{SEND_MINUTE:02d}（台灣時間）",
        inline=False,
    )
    embed.add_field(name="下次執行時間", value=next_str, inline=False)
    embed.add_field(name="發送頻道 ID", value=str(DISCORD_CHANNEL_ID), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="help", aliases=["說明", "幫助"])
async def cmd_help(ctx: commands.Context) -> None:
    """顯示指令說明"""
    embed = discord.Embed(title="📋 指令說明", color=discord.Color.blurple())
    embed.add_field(
        name="!foreign 或 !外資",
        value="手動抓取並發送外資買賣超排行",
        inline=False,
    )
    embed.add_field(
        name="!trust 或 !投信",
        value="手動抓取並發送投信買賣超排行",
        inline=False,
    )
    embed.add_field(
        name="!report 或 !報告",
        value="同時發送外資與投信報告",
        inline=False,
    )
    embed.add_field(
        name="!status 或 !狀態",
        value="顯示 Bot 狀態與下次排程時間",
        inline=False,
    )
    embed.set_footer(text=f"每日 {SEND_HOUR:02d}:{SEND_MINUTE:02d}（台灣時間）自動發送報告")
    await ctx.send(embed=embed)


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("請在 .env 中設定 DISCORD_TOKEN")
    if not DISCORD_CHANNEL_ID:
        raise ValueError("請在 .env 中設定 DISCORD_CHANNEL_ID")
    bot.run(DISCORD_TOKEN, log_handler=None)
