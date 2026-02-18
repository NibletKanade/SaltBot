from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.params import CommandArg

import sqlite3
import os
import re
from typing import Optional, List, Tuple

ping = on_command("ping", priority=5)
# setcard = on_command("改名", priority=5)

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
    # 创建表（如果不存在）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS members (
        group_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        season_pts INTEGER DEFAULT 0,
        season_rank INTEGER DEFAULT 0,
        round_pts INTEGER DEFAULT 0,
        round_rank INTEGER DEFAULT 0,
        libido INTEGER DEFAULT 0,
        rc INTEGER DEFAULT 0,
        yc INTEGER DEFAULT 0,
        PRIMARY KEY (group_id, user_id)
    )
    """)
    conn.commit()
    # 确保必须的列存在（用于从旧 schema 升级）
    cur.execute("PRAGMA table_info(members)")
    existing = {row[1] for row in cur.fetchall()}
    needed = {
        'season_pts': 'INTEGER DEFAULT 0',
        'season_rank': 'INTEGER DEFAULT 0',
        'round_pts': 'INTEGER DEFAULT 0',
        'round_rank': 'INTEGER DEFAULT 0',
        'libido': 'INTEGER DEFAULT 0',
        'rc': 'INTEGER DEFAULT 0',
        'yc': 'INTEGER DEFAULT 0'
    }
    for col, col_def in needed.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE members ADD COLUMN {col} {col_def}")
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
    # 支持 season_pts, round_pts, libido, rc, yc
    allowed = {"season_pts", "round_pts", "libido", "rc", "yc"}
    if field not in allowed:
        raise ValueError("invalid field")
    conn = get_conn()
    cur = conn.cursor()
    # 确保行存在
    cur.execute("INSERT OR IGNORE INTO members (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
    cur.execute(f"UPDATE members SET {field} = ? WHERE group_id = ? AND user_id = ?", (value, group_id, user_id))
    conn.commit()
    conn.close()

def add_field(group_id: int, user_id: int, field: str, delta: int) -> None:
    allowed = {"season_pts", "round_pts", "libido", "rc", "yc"}
    if field not in allowed:
        raise ValueError("invalid field")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO members (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
    cur.execute(f"UPDATE members SET {field} = COALESCE({field}, 0) + ? WHERE group_id = ? AND user_id = ?", (delta, group_id, user_id))
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
    # season ranks by season_pts
    cur.execute("SELECT user_id, season_pts FROM members WHERE group_id = ? ORDER BY season_pts DESC", (group_id,))
    rows = cur.fetchall()
    rank = 0
    prev_pts = None
    pos = 0
    for r in rows:
        pos += 1
        if prev_pts is None or r["season_pts"] != prev_pts:
            rank = pos
        prev_pts = r["season_pts"]
        cur.execute("UPDATE members SET season_rank = ? WHERE group_id = ? AND user_id = ?", (rank, group_id, r["user_id"]))
    # round ranks by round_pts
    cur.execute("SELECT user_id, round_pts FROM members WHERE group_id = ? ORDER BY round_pts DESC", (group_id,))
    rows = cur.fetchall()
    rank = 0
    prev_pts = None
    pos = 0
    for r in rows:
        pos += 1
        if prev_pts is None or r["round_pts"] != prev_pts:
            rank = pos
        prev_pts = r["round_pts"]
        cur.execute("UPDATE members SET round_rank = ? WHERE group_id = ? AND user_id = ?", (rank, group_id, r["user_id"]))
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

def _compute_round_medal_map(group_id: int) -> dict:
    """返回 {user_id: emoji}，根据 round_rank 分配前三个不同的名次作为🥇🥈🥉，并列共享荣誉，顺延下一级。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, round_rank FROM members WHERE group_id = ? ORDER BY round_rank ASC", (group_id,))
    rows = cur.fetchall()
    medals = ["🥇", "🥈", "🥉"]
    distinct_ranks = []
    for r in rows:
        rr = r["round_rank"]
        if rr not in distinct_ranks:
            distinct_ranks.append(rr)
    rank_to_medal = {}
    for i, rr in enumerate(distinct_ranks[:3]):
        rank_to_medal[rr] = medals[i]
    user_medal = {}
    for r in rows:
        m = rank_to_medal.get(r["round_rank"]) 
        if m:
            user_medal[r["user_id"]] = m
    conn.close()
    return user_medal

async def refresh_cardname(bot: Bot, group_id: int) -> tuple:
    """更新指定群的所有已注册成员的群名片，返回 (success_count, fail_count)。"""
    # 先确保排名已计算
    recompute_ranks(group_id)
    rows = get_all_members(group_id)
    medal_map = _compute_round_medal_map(group_id)
    success = 0
    fail = 0
    for r in rows:
        user_id = r["user_id"]
        # sqlite3.Row 不支持 .get，使用键索引并处理 None
        season_pts = r['season_pts'] or 0
        season_rank = r['season_rank'] or 0
        round_pts = r['round_pts'] or 0
        round_rank = r['round_rank'] or 0
        libido = r['libido'] or 0
        rc = r['rc'] or 0
        yc = r['yc'] or 0
        # 获取当前群成员信息以取昵称/群名片
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
        medal = medal_map.get(user_id, "")
        s_rank_str = f"({season_rank})" if season_rank else "(0)"
        if round_rank:
            r_rank_str = f"({round_rank}{medal})"
        else:
            r_rank_str = "(0)"
        # 不再把赛季分数与排名写入群名片，仅保留回合分数（含奖牌）与其他字段
        new_card = f"{base}[R-pts{round_pts}{r_rank_str} Libido{libido} RC{rc} YC{yc}]"
        try:
            await bot.set_group_card(group_id=group_id, user_id=user_id, card=new_card)
            success += 1
        except Exception:
            fail += 1
            continue
    return success, fail

# --- Commands: register / unregister / setSpts / addSpts / setRpts / addRpts / setlibido / setrc / setyc / showall / show / refreshcards / help ---

register = on_command("register", priority=5)
unregister = on_command("unregister", priority=5)
setSpts = on_command("setSpts", priority=5)
addSpts = on_command("addSpts", priority=5)
setRpts = on_command("setRpts", priority=5)
addRpts = on_command("addRpts", priority=5)
setlibido = on_command("setlibido", priority=5)
setrc = on_command("setrc", priority=5)
setyc = on_command("setyc", priority=5)
addlibido = on_command("addlibido", priority=5)
addrc = on_command("addrc", priority=5)
addyc = on_command("addyc", priority=5)
showall = on_command("showall", priority=10)
show = on_command("show", priority=10)
refreshcards_cmd = on_command("refreshcards", priority=5)
help_cmd = on_command("help", priority=10)
# 新增排行榜命令
seasonboard = on_command("seasonboard", priority=10)
roundboard = on_command("roundboard", priority=10)

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
    try:
        suc, fail = await refresh_cardname(bot, event.group_id)
    except Exception as e:
        # 确保有回复，记录失败
        suc, fail = 0, 0
    display = await get_display_name(bot, event.group_id, user_id)
    await register.send(f"{display} 已注册；名片刷新：{suc} 成功，{fail} 失败")
    return

@unregister.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    user_id = extract_at_user(event) or event.user_id
    remove_member(event.group_id, user_id)
    recompute_ranks(event.group_id)
    try:
        suc, fail = await refresh_cardname(bot, event.group_id)
    except Exception:
        suc, fail = 0, 0
    display = await get_display_name(bot, event.group_id, user_id)
    await unregister.send(f"{display} 已从注册名单移除；名片刷新：{suc} 成功，{fail} 失败")
    return

async def _parse_value(args_text: str) -> Optional[int]:
    try:
        return int(args_text.strip())
    except Exception:
        return None

@setSpts.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setSpts.send("用法：#setSpts @user 123")
        return
    set_field(event.group_id, user_id, "season_pts", val)
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setSpts.send(f"已将 {display} 的 Season Pts 设置为 {val}")
    return

@addSpts.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    delta = 1
    if text:
        try:
            delta = int(text)
        except Exception:
            await addSpts.send("用法：#addSpts @user [数量]（默认 1）")
            return
    add_field(event.group_id, user_id, "season_pts", delta)
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    r = get_member(event.group_id, user_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await addSpts.send(f"已为 {display} 增加 Season Pts {delta}，当前 S-pts={r['season_pts']} S-rank={r['season_rank']}")
    return

@setRpts.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setRpts.send("用法：#setRpts @user 123")
        return
    set_field(event.group_id, user_id, "round_pts", val)
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setRpts.send(f"已将 {display} 的 Round Pts 设置为 {val}")
    return

@addRpts.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    delta = 1
    if text:
        try:
            delta = int(text)
        except Exception:
            await addRpts.send("用法：#addRpts @user [数量]（默认 1）")
            return
    add_field(event.group_id, user_id, "round_pts", delta)
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    r = get_member(event.group_id, user_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await addRpts.send(f"已为 {display} 增加 Round Pts {delta}，当前 R-pts={r['round_pts']} R-rank={r['round_rank']}")
    return

@setlibido.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setlibido.send("用法：#setlibido @user 123")
        return
    add_member(event.group_id, user_id)
    set_field(event.group_id, user_id, "libido", val)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setlibido.send(f"已将 {display} 的 Libido 设置为 {val}")
    return

@setrc.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setrc.send("用法：#setrc @user 1")
        return
    add_member(event.group_id, user_id)
    set_field(event.group_id, user_id, "rc", val)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setrc.send(f"已将 {display} 的 RC 设置为 {val}")
    return

@setyc.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    val = await _parse_value(text)
    if val is None:
        await setyc.send("用法：#setyc @user 1")
        return
    add_member(event.group_id, user_id)
    set_field(event.group_id, user_id, "yc", val)
    await refresh_cardname(bot, event.group_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await setyc.send(f"已将 {display} 的 YC 设置为 {val}")
    return

@addlibido.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    delta = 1
    if text:
        try:
            delta = int(text)
        except Exception:
            await addlibido.send("用法：#addlibido @user [数量]（默认 1）")
            return
    add_field(event.group_id, user_id, "libido", delta)
    await refresh_cardname(bot, event.group_id)
    r = get_member(event.group_id, user_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await addlibido.send(f"已为 {display} 增加 Libido {delta}，当前 Libido={r['libido']}")
    return

@addrc.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    delta = 1
    if text:
        try:
            delta = int(text)
        except Exception:
            await addrc.send("用法：#addrc @user [数量]（默认 1）")
            return
    add_field(event.group_id, user_id, "rc", delta)
    await refresh_cardname(bot, event.group_id)
    r = get_member(event.group_id, user_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await addrc.send(f"已为 {display} 增加 RC {delta}，当前 RC={r['rc']}")
    return

@addyc.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    user_id = extract_at_user(event) or event.user_id
    text = args.extract_plain_text().strip()
    delta = 1
    if text:
        try:
            delta = int(text)
        except Exception:
            await addyc.send("用法：#addyc @user [数量]（默认 1）")
            return
    add_field(event.group_id, user_id, "yc", delta)
    await refresh_cardname(bot, event.group_id)
    r = get_member(event.group_id, user_id)
    display = await get_display_name(bot, event.group_id, user_id)
    await addyc.send(f"已为 {display} 增加 YC {delta}，当前 YC={r['yc']}")
    return

@showall.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    # 先确保排名和奖牌为最新
    recompute_ranks(event.group_id)
    medal_map = _compute_round_medal_map(event.group_id)
    rows = get_all_members(event.group_id)
    if not rows:
        await showall.send("没有注册的群友")
        return
    lines = []
    for r in rows:
        display = await get_display_name(bot, event.group_id, r['user_id'])
        s_pts = r['season_pts'] or 0
        s_rank = r['season_rank'] or 0
        r_pts = r['round_pts'] or 0
        r_rank = r['round_rank'] or 0
        medal = medal_map.get(r['user_id'], '')
        lines.append(f"{display}: S-pts={s_pts}({s_rank}) R-pts={r_pts}({r_rank}{medal}) Libido={r['libido'] or 0} RC={r['rc'] or 0} YC={r['yc'] or 0}")
    await showall.send("\n".join(lines))
    return

@show.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    user_id = extract_at_user(event) or event.user_id
    r = get_member(event.group_id, user_id)
    if not r:
        await show.send("该用户未注册")
        return
    display = await get_display_name(bot, event.group_id, user_id)
    s_pts = r['season_pts'] or 0
    s_rank = r['season_rank'] or 0
    r_pts = r['round_pts'] or 0
    r_rank = r['round_rank'] or 0
    await show.send(f"{display}: S-pts={s_pts}({s_rank}) R-pts={r_pts}({r_rank}) Libido={r['libido'] or 0} RC={r['rc'] or 0} YC={r['yc'] or 0}")
    return

@refreshcards_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    try:
        suc, fail = await refresh_cardname(bot, event.group_id)
        await refreshcards_cmd.send(f"名片刷新完成：{suc} 成功，{fail} 失败")
        return
    except Exception as e:
        await refreshcards_cmd.send("名片刷新过程中出现错误，已中止")
        return

# 保留原有的 ping 与 setcard 处理

@ping.handle()
async def _(event: MessageEvent):
    await ping.send("pong")
    return

# @setcard.handle()
# async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
#     # 取 at 的用户
#     if not event.message:
#         await setcard.finish("用法：#改名 @某人 新昵称")

#     at = next((seg for seg in event.message if seg.type == "at"), None)
#     if not at:
#         await setcard.finish("请 @ 一个群成员")

#     user_id = int(at.data["qq"])
#     nickname = args.extract_plain_text().strip()

#     if not nickname:
#         await setcard.finish("请输入新的群昵称")

#     await bot.set_group_card(
#         group_id=event.group_id,
#         user_id=user_id,
#         card=nickname
#     )

#     await setcard.finish(
#         MessageSegment.at(user_id) + f" 的群昵称已修改为：{nickname}"
#     )

@help_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    msg = (
        "功能列表：\n"
        "#register @user — 注册用户（不带 @ 则注册自己）\n"
        "#unregister @user — 注销用户（不带 @ 则注销自己）\n"
        "#setSpts @user 123 — 设置赛季分数 Season Pts\n"
        "#addSpts @user [数量] — 增加赛季分数，默认 1\n"
        "#setRpts @user 123 — 设置回合分数 Round Pts\n"
        "#addRpts @user [数量] — 增加回合分数，默认 1\n"
        "#setlibido @user 5 — 设置 Libido\n"
        "#addlibido @user [数量] — 增加 Libido，默认 1\n"
        "#setrc @user 1 — 设置红牌（RC）\n"
        "#addrc @user [数量] — 增加 RC，默认 1\n"
        "#setyc @user 2 — 设置黄牌（YC）\n"
        "#addyc @user [数量] — 增加 YC，默认 1\n"
        "#show @user — 显示特定群友的数据（不带 @ 则显示自己）\n"
        "#showall — 列出本群所有已注册群友的数据\n"
        "#seasonboard — 显示赛季积分榜\n"
        "#roundboard — 显示回合积分榜\n"
        "#refreshcards — 手动刷新所有注册成员的群名片\n"
        "#help — 显示本帮助信息\n"
    )
    await help_cmd.send(msg)
    return

@seasonboard.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    # 按照赛季分数排序显示
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, season_pts, season_rank FROM members WHERE group_id = ? ORDER BY season_rank ASC", (event.group_id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await seasonboard.send("当前没有注册的用户")
        return
    lines = []
    for r in rows:
        display = await get_display_name(bot, event.group_id, r['user_id'])
        lines.append(f"{display}: S-pts={r['season_pts']} (Rank {r['season_rank']})")
    await seasonboard.send("赛季积分榜：\n" + "\n".join(lines))
    return

@roundboard.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    # 确保排名和奖牌为最新
    recompute_ranks(event.group_id)
    medal_map = _compute_round_medal_map(event.group_id)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, round_pts, round_rank FROM members WHERE group_id = ? ORDER BY round_rank ASC", (event.group_id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await roundboard.send("当前没有注册的用户")
        return
    lines = []
    for r in rows:
        display = await get_display_name(bot, event.group_id, r['user_id'])
        medal = medal_map.get(r['user_id'], '')
        lines.append(f"{display}: R-pts={r['round_pts']} (Rank {r['round_rank']}) {medal}")
    await roundboard.send("回合积分榜：\n" + "\n".join(lines))
    return