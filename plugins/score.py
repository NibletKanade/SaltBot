from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.params import CommandArg

import sqlite3
import os
import re
from typing import Optional, List, Tuple

ping = on_command("ping", priority=5)
setcard = on_command("改名", priority=5)

# Database file
DB_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.abspath(os.path.join(DB_DIR, "saltbot.db"))

os.makedirs(DB_DIR, exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS members (
        group_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        pts INTEGER DEFAULT 0,
        rank INTEGER DEFAULT 0,
        libido INTEGER DEFAULT 0,
        rc INTEGER DEFAULT 0,
        yc INTEGER DEFAULT 0,
        PRIMARY KEY (group_id, user_id)
    )
    """)
    conn.commit()
    conn.close()

init_db()

# --- DB helpers ---

def add_member(group_id: int, user_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO members (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
    conn.commit()
    conn.close()

def remove_member(group_id: int, user_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM members WHERE group_id = ? AND user_id = ?", (group_id, user_id))
    conn.commit()
    conn.close()

def set_field(group_id: int, user_id: int, field: str, value: int) -> None:
    if field not in {"pts", "libido", "rc", "yc"}:
        raise ValueError("invalid field")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE members SET {field} = ? WHERE group_id = ? AND user_id = ?", (value, group_id, user_id))
    conn.commit()
    conn.close()

def get_member(group_id: int, user_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM members WHERE group_id = ? AND user_id = ?", (group_id, user_id))
    row = cur.fetchone()
    conn.close()
    return row

def get_all_members(group_id: int) -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM members WHERE group_id = ?", (group_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def recompute_ranks(group_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, pts FROM members WHERE group_id = ? ORDER BY pts DESC", (group_id,))
    rows = cur.fetchall()
    rank = 0
    prev_pts = None
    pos = 0
    for r in rows:
        pos += 1
        if prev_pts is None or r["pts"] != prev_pts:
            rank = pos
        prev_pts = r["pts"]
        cur.execute("UPDATE members SET rank = ? WHERE group_id = ? AND user_id = ?", (rank, group_id, r["user_id"]))
    conn.commit()
    conn.close()

# strip existing bracket suffix like 'Name[...']'
_bracket_re = re.compile(r"\s*\[.*\]$")

async def get_display_name(bot: Bot, group_id: int, user_id: int) -> str:
    """返回用户的群名片或昵称，并去掉尾部方括号内的统计后缀；若获取失败返回 qq 号字符串。"""
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id, no_cache=True)
        if isinstance(info, dict):
            base = info.get("card") or info.get("nickname") or ""
        else:
            base = ""
    except Exception:
        base = ""
    base = (base or "").strip()
    base = _bracket_re.sub("", base).strip()
    return base if base else str(user_id)

async def refresh_cardname(bot: Bot, group_id: int) -> None:
    """更新指定群的所有已注册成员的群名片。"""
    rows = get_all_members(group_id)
    for r in rows:
        user_id = r["user_id"]
        pts = r["pts"]
        rank = r["rank"]
        libido = r["libido"]
        rc = r["rc"]
        yc = r["yc"]
        # 获取当前群成员信息以取昵称/群名片
        try:
            info = await bot.get_group_member_info(group_id=group_id, user_id=user_id, no_cache=True)
            if isinstance(info, dict):
                base = info.get("card") or info.get("nickname") or ""
            else:
                base = ""
        except Exception:
            base = ""
        base = base.strip()
        # 去掉上一次可能附加的方括号内容
        base = _bracket_re.sub("", base).strip()
        new_card = f"{base}[Pts{pts} RANK{rank} Libido{libido} RC{rc} YC{yc}]"
        try:
            await bot.set_group_card(group_id=group_id, user_id=user_id, card=new_card)
        except Exception:
            # 忽略单个修改失败，继续下一个
            continue

# --- Commands: register / unregister / setpts / setlibido / setrc / setyc / showall / show / refreshcards ---

register = on_command("register", priority=5)
unregister = on_command("unregister", priority=5)
setpts = on_command("setpts", priority=5)
setlibido = on_command("setlibido", priority=5)
setrc = on_command("setrc", priority=5)
setyc = on_command("setyc", priority=5)
showall = on_command("showall", priority=10)
show = on_command("show", priority=10)
refreshcards_cmd = on_command("refreshcards", priority=5)
help_cmd = on_command("help", priority=10)

# helper to parse @ as in previous code
def extract_at_user(event: GroupMessageEvent) -> Optional[int]:
    if not event.message:
        return None
    at_seg = next((seg for seg in event.message if seg.type == "at"), None)
    if not at_seg:
        return None
    return int(at_seg.data.get("qq"))

@register.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    user_id = extract_at_user(event) or event.user_id
    add_member(event.group_id, user_id)
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await register.finish(f"{display} 已注册")

@unregister.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    user_id = extract_at_user(event) or event.user_id
    remove_member(event.group_id, user_id)
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await unregister.finish(f"{display} 已从注册名单移除")

async def _parse_value(args_text: str) -> Optional[int]:
    try:
        return int(args_text.strip())
    except Exception:
        return None

@setpts.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setpts.finish("用法：#setpts @user 123")
    add_member(event.group_id, user_id)
    set_field(event.group_id, user_id, "pts", val)
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setpts.finish(f"已将 {display} 的 Pts 设置为 {val}")

@setlibido.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setlibido.finish("用法：#setlibido @user 123")
    add_member(event.group_id, user_id)
    set_field(event.group_id, user_id, "libido", val)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setlibido.finish(f"已将 {display} 的 Libido 设置为 {val}")

@setrc.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setrc.finish("用法：#setrc @user 1")
    add_member(event.group_id, user_id)
    set_field(event.group_id, user_id, "rc", val)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setrc.finish(f"已将 {display} 的 RC 设置为 {val}")

@setyc.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setyc.finish("用法：#setyc @user 1")
    add_member(event.group_id, user_id)
    set_field(event.group_id, user_id, "yc", val)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setyc.finish(f"已将 {display} 的 YC 设置为 {val}")

@showall.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    rows = get_all_members(event.group_id)
    if not rows:
        await showall.finish("没有注册的群友")
    lines = []
    for r in rows:
        display = await get_display_name(bot, event.group_id, r['user_id'])
        lines.append(f"{display}: Pts={r['pts']} RANK={r['rank']} Libido={r['libido']} RC={r['rc']} YC={r['yc']}")
    await showall.finish("\n".join(lines))

@show.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    user_id = extract_at_user(event) or event.user_id
    r = get_member(event.group_id, user_id)
    if not r:
        await show.finish("该用户未注册")
    display = await get_display_name(bot, event.group_id, user_id)
    await show.finish(f"{display}: Pts={r['pts']} RANK={r['rank']} Libido={r['libido']} RC={r['rc']} YC={r['yc']}")

@refreshcards_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    await refresh_cardname(bot, event.group_id)
    await refreshcards_cmd.finish("群名片已刷新")

# 保留原有的 ping 与 setcard 处理

@ping.handle()
async def _(event: MessageEvent):
    await ping.finish("pong")

@setcard.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    # 取 at 的用户
    if not event.message:
        await setcard.finish("用法：#改名 @某人 新昵称")

    at = next((seg for seg in event.message if seg.type == "at"), None)
    if not at:
        await setcard.finish("请 @ 一个群成员")

    user_id = int(at.data["qq"])
    nickname = args.extract_plain_text().strip()

    if not nickname:
        await setcard.finish("请输入新的群昵称")

    await bot.set_group_card(
        group_id=event.group_id,
        user_id=user_id,
        card=nickname
    )

    await setcard.finish(
        MessageSegment.at(user_id) + f" 的群昵称已修改为：{nickname}"
    )

@help_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    msg = (
        "功能列表：\n"
        "#register @user — 注册用户（不带 @ 则注册自己）\n"
        "#unregister @user — 注销用户（不带 @ 则注销自己）\n"
        "#setpts @user 123 — 设置 Pts\n"
        "#setlibido @user 5 — 设置 Libido\n"
        "#setrc @user 1 — 设置红牌（RC）\n"
        "#setyc @user 2 — 设置黄牌（YC）\n"
        "#show @user — 显示特定群友的数据（不带 @ 则显示自己）\n"
        "#showall — 列出本群所有已注册群友的数据\n"
        "#refreshcards — 手动刷新所有注册成员的群名片\n"
        "#help — 显示本帮助信息\n"
    )
    await help_cmd.finish(msg)