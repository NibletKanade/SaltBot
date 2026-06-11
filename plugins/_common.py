import sqlite3
import os
import re
from typing import Optional, List, Tuple
from datetime import datetime
from nonebot.adapters.onebot.v11 import Bot

# Database file (shared)
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
    # members 表（如果不存在）
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
        round_pred_pts INTEGER DEFAULT 0,
        PRIMARY KEY (group_id, user_id)
    )
    """)
    conn.commit()
    # 确保必须的列存在（向后兼容）
    cur.execute("PRAGMA table_info(members)")
    existing = {row[1] for row in cur.fetchall()}
    needed = {
        'season_pts': 'INTEGER DEFAULT 0',
        'season_rank': 'INTEGER DEFAULT 0',
        'round_pts': 'INTEGER DEFAULT 0',
        'round_rank': 'INTEGER DEFAULT 0',
        'libido': 'INTEGER DEFAULT 0',
        'rc': 'INTEGER DEFAULT 0',
        'yc': 'INTEGER DEFAULT 0',
        'round_pred_pts': 'INTEGER DEFAULT 0',
    }
    for col, col_def in needed.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE members ADD COLUMN {col} {col_def}")
    conn.commit()
    conn.close()

# call init on import
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

def set_field(group_id: int, user_id: int, field: str, value: int, conn: sqlite3.Connection = None) -> None:
    allowed = {"season_pts", "round_pts", "libido", "rc", "yc", "round_pred_pts"}
    if field not in allowed:
        raise ValueError("invalid field")
    close_conn = False
    if conn is None:
        conn = get_conn()
        close_conn = True
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO members (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
    cur.execute(f"UPDATE members SET {field} = ? WHERE group_id = ? AND user_id = ?", (value, group_id, user_id))
    if close_conn:
        conn.commit()
        conn.close()

def add_field(group_id: int, user_id: int, field: str, delta: int, conn: sqlite3.Connection = None) -> None:
    allowed = {"season_pts", "round_pts", "libido", "rc", "yc", "round_pred_pts"}
    if field not in allowed:
        raise ValueError("invalid field")
    close_conn = False
    if conn is None:
        conn = get_conn()
        close_conn = True
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO members (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
    cur.execute(f"UPDATE members SET {field} = COALESCE({field}, 0) + ? WHERE group_id = ? AND user_id = ?", (delta, group_id, user_id))
    if close_conn:
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
    recompute_ranks(group_id)
    rows = get_all_members(group_id)
    medal_map = _compute_round_medal_map(group_id)
    success = 0
    fail = 0
    for r in rows:
        user_id = r["user_id"]
        season_pts = r['season_pts'] or 0
        season_rank = r['season_rank'] or 0
        round_pts = r['round_pts'] or 0
        round_rank = r['round_rank'] or 0
        libido = r['libido'] or 0
        rc = r['rc'] or 0
        yc = r['yc'] or 0
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
        if round_rank:
            r_rank_str = f"({round_rank}{medal})"
        else:
            r_rank_str = "(0)"
        new_card = f"{base}[R-pts{round_pts}{r_rank_str} Libido{libido} RC{rc} YC{yc}]"
        try:
            await bot.set_group_card(group_id=group_id, user_id=user_id, card=new_card)
            success += 1
        except Exception:
            fail += 1
            continue
    return success, fail

# 通用修改并刷新函数
from typing import Optional as _Opt
async def modify_and_refresh(bot: Bot, group_id: int, user_id: int, field: str, *, add: _Opt[int] = None, set: _Opt[int] = None) -> Tuple[Optional[sqlite3.Row], int, int]:
    if add is None and set is None:
        raise ValueError("either add or set must be provided")
    if field not in {"season_pts", "round_pts", "libido", "rc", "yc", "round_pred_pts"}:
        raise ValueError("invalid field")
    if add is not None:
        add_field(group_id, user_id, field, add)
    else:
        set_field(group_id, user_id, field, set)
    recompute_ranks(group_id)
    suc, fail = await refresh_cardname(bot, group_id)
    row = get_member(group_id, user_id)
    return row, suc, fail

# 初始化其他需要的新表将由 roundsystem.py 负责创建
