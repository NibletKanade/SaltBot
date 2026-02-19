from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.params import CommandArg

import sqlite3
import os
import csv
import re
from datetime import datetime
from typing import Optional, List, Tuple

from ._common import get_conn, init_db, add_member, get_member, set_field, add_field, recompute_ranks, refresh_cardname, get_display_name

# 辅助函数：从消息中提取 @ 提及的用户 ID
def extract_at_user(event) -> Optional[int]:
    """从群消息事件中提取第一个 @ 提及的用户 QQ 号"""
    if not event.message:
        return None
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq:
                return int(qq)
    return None

# create tables for rounds, matches, predictions, settlements
def init_round_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER,
        name TEXT,
        status TEXT DEFAULT 'open',
        created_by INTEGER,
        created_at TEXT,
        ended_at TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id INTEGER,
        idx INTEGER,
        home TEXT,
        away TEXT,
        result_home INTEGER,
        result_away INTEGER,
        result_set_by INTEGER,
        result_set_at TEXT
    )
    ''')
    # ensure (round_id, idx) unique to avoid duplicate sequence numbers
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_matches_round_idx ON matches(round_id, idx)")
    cur.execute('''
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER,
        group_id INTEGER,
        user_id INTEGER,
        pred_home INTEGER,
        pred_away INTEGER,
        awarded_points INTEGER DEFAULT 0,
        updated_at TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER,
        round_id INTEGER,
        match_id INTEGER,
        user_id INTEGER,
        points_awarded INTEGER,
        reason TEXT,
        ts TEXT
    )
    ''')
    conn.commit()
    conn.close()

init_round_db()

# helper: get active round for group
def get_active_round(group_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rounds WHERE group_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1", (group_id,))
    r = cur.fetchone()
    conn.close()
    return r

# commands
startround = on_command("startround", priority=5)
addmatch = on_command("addmatch", priority=5)
listmatches = on_command("listmatches", priority=10)
setmatchresult = on_command("setmatchresult", priority=5)
endround = on_command("endround", priority=5)
abortround = on_command("abortround", priority=5)
predict = on_command("predict", priority=10)
matchpreds = on_command("matchpreds", priority=10)
mypreds = on_command("mypreds", priority=10)
exportsettlements = on_command("exportsettlements", priority=5)
setpred = on_command("setpred", priority=5)
# 新增批量添加与批量预测命令
addmatches = on_command("addmatches", priority=5)
predicts = on_command("predicts", priority=10)

# permission helper
def is_admin(event: GroupMessageEvent) -> bool:
    try:
        role = event.sender.role
        return role in ("owner", "admin")
    except Exception:
        return False

# compare_prediction placeholder (B: adjustments are applied to actual result)
def compare_prediction(actual_h: int, actual_a: int, pred_h: int, pred_a: int) -> int:
    res = 0
    if pred_h == actual_h:
        res += 2
    elif abs(pred_h - actual_h) == 1:
        res += 1
    
    if pred_a == actual_a:
        res += 2
    elif abs(pred_a - actual_a) == 1 and abs(pred_h - actual_h) != 1:
        res += 1

    actual_diff = actual_a - actual_h
    if actual_diff > 5:
        actual_diff = 5
    if actual_diff < -5:
        actual_diff = -5

    pred_diff = pred_a - pred_h
    if pred_diff > 5:
        pred_diff = 5
    if pred_diff < -5:
        pred_diff = -5

    if pred_diff == actual_diff:
        res += 2
    elif (pred_diff) * (actual_diff) > 0 and abs((pred_diff) - (actual_diff)) <= 2:
        res += 1

    if (pred_diff) * (actual_diff) <= 0 and abs((pred_diff) - (actual_diff)) >= 2 and res > 0:
        res -= 1

    return res

# CRUD helpers for matches and predictions
def add_round(group_id: int, name: str, created_by: int) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO rounds (group_id, name, status, created_by, created_at) VALUES (?, ?, 'open', ?, ?)", (group_id, name, created_by, datetime.utcnow().isoformat()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid

def add_match_to_round(round_id: int, idx: Optional[int], home: str, away: str) -> int:
    """如果 idx 为 None，则自动分配为当前回合中最大的 idx + 1（或从1开始）。返回实际分配的 idx。"""
    conn = get_conn()
    cur = conn.cursor()
    if idx is None:
        cur.execute("SELECT MAX(idx) as mx FROM matches WHERE round_id = ?", (round_id,))
        r = cur.fetchone()
        mx = r['mx'] if r and 'mx' in r.keys() and r['mx'] is not None else 0
        idx = (mx or 0) + 1
    assigned_idx = idx
    try:
        cur.execute("INSERT INTO matches (round_id, idx, home, away) VALUES (?, ?, ?, ?)", (round_id, idx, home, away))
        conn.commit()
    except sqlite3.IntegrityError:
        # 发生唯一约束冲突（并发或相同 idx），在这种情况下尝试再次获取新的 idx 并重试一次
        cur.execute("SELECT MAX(idx) as mx FROM matches WHERE round_id = ?", (round_id,))
        r = cur.fetchone()
        mx = r['mx'] if r and 'mx' in r.keys() and r['mx'] is not None else 0
        idx = (mx or 0) + 1
        cur.execute("INSERT INTO matches (round_id, idx, home, away) VALUES (?, ?, ?, ?)", (round_id, idx, home, away))
        conn.commit()
        assigned_idx = idx
    finally:
        conn.close()
    return assigned_idx

def list_matches_of_round(round_id: int) -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM matches WHERE round_id = ? ORDER BY idx ASC", (round_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_match(round_id: int, idx: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM matches WHERE round_id = ? AND idx = ?", (round_id, idx))
    r = cur.fetchone()
    conn.close()
    return r

def upsert_prediction(group_id: int, match_id: int, user_id: int, pred_h: int, pred_a: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    # 查看是否已有该用户该比赛的预测记录
    cur.execute("SELECT id FROM predictions WHERE match_id = ? AND user_id = ?", (match_id, user_id))
    row = cur.fetchone()
    now = datetime.utcnow().isoformat()
    if row is not None and row['id'] is not None:
        # 已有则更新预测与时间戳（保留已有的调整和已发分）
        cur.execute("UPDATE predictions SET pred_home = ?, pred_away = ?, updated_at = ? WHERE id = ?", (pred_h, pred_a, now, row['id']))
    else:
        # 否则插入新记录，初始化调整与已发分为0
        cur.execute("INSERT INTO predictions (match_id, group_id, user_id, pred_home, pred_away, awarded_points, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (match_id, group_id, user_id, pred_h, pred_a, now))
    conn.commit()
    conn.close()

# set match result and settle points (admin only)
def set_match_result_db(group_id: int, idx: int, actual_h: int, actual_a: int, set_by: int) -> Tuple[int, int]:
    """返回 (suc_count, fail_count) - suc/fail refer to prediction processing count successes and failures (mostly DB side should be fine)"""
    active = get_active_round(group_id)
    if not active:
        raise ValueError("no active round")
    round_id = active['id']
    m = get_match(round_id, idx)
    if not m:
        raise ValueError("match not found")
    conn = get_conn()
    cur = conn.cursor()
    # update match result
    cur.execute("UPDATE matches SET result_home = ?, result_away = ?, result_set_by = ?, result_set_at = ? WHERE id = ?", (actual_h, actual_a, set_by, datetime.utcnow().isoformat(), m['id']))
    # fetch predictions
    cur.execute("SELECT * FROM predictions WHERE match_id = ?", (m['id'],))
    preds = cur.fetchall()
    suc = 0
    fail = 0
    for p in preds:
        try:
            # per user adjusted actual: if adjust columns existed previously they were migrated away, so treat as 0
            adjust_h = p['adjust_home'] if 'adjust_home' in p.keys() and p['adjust_home'] is not None else 0
            adjust_a = p['adjust_away'] if 'adjust_away' in p.keys() and p['adjust_away'] is not None else 0
            adj_actual_h = (actual_h or 0) + (adjust_h or 0)
            adj_actual_a = (actual_a or 0) + (adjust_a or 0)
            points = compare_prediction(adj_actual_h, adj_actual_a, p['pred_home'] or 0, p['pred_away'] or 0)
            old_points = p['awarded_points'] or 0
            delta = points - old_points
            if delta != 0:
                # 使用同一个连接更新 round_pts 与 round_pred_pts，避免锁问题
                add_field(group_id, p['user_id'], 'round_pts', delta, conn=conn)
                add_field(group_id, p['user_id'], 'round_pred_pts', delta, conn=conn)
                # record settlement
                cur.execute("INSERT INTO settlements (group_id, round_id, match_id, user_id, points_awarded, reason, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (group_id, round_id, m['id'], p['user_id'], delta, f"match_{idx}_settle", datetime.utcnow().isoformat()))
            # update awarded_points
            cur.execute("UPDATE predictions SET awarded_points = ?, updated_at = ? WHERE id = ?", (points, datetime.utcnow().isoformat(), p['id']))
            suc += 1
        except Exception as e:
            # 打印异常堆栈以便定位具体失败原因（例如类型/索引错误）
            import traceback
            try:
                pid = p['id'] if p is not None and 'id' in p.keys() else None
            except Exception:
                pid = None
            try:
                p_user = p['user_id'] if p is not None and 'user_id' in p.keys() else None
            except Exception:
                p_user = None
            print(f"Error processing prediction id={pid} user={p_user}: {e}")
            traceback.print_exc()
            fail += 1
            continue
    conn.commit()
    conn.close()
    # after batch processing, recompute and refresh
    recompute_ranks(group_id)
    # Note: refresh_cardname is async; caller should call it
    return suc, fail

# end round: award season points to top3 according to round_rank
def end_round_db(group_id: int, ended_by: int) -> Tuple[int, int]:
    active = get_active_round(group_id)
    if not active:
        raise ValueError("no active round")
    round_id = active['id']
    # recompute ranks to ensure fresh
    recompute_ranks(group_id)
    conn = get_conn()
    cur = conn.cursor()
    # get users ordered by round_rank asc
    cur.execute("SELECT user_id, round_rank FROM members WHERE group_id = ? ORDER BY round_rank ASC", (group_id,))
    rows = cur.fetchall()
    if not rows:
        cur.execute("UPDATE rounds SET status = 'ended', ended_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), round_id))
        conn.commit()
        conn.close()
        return 0, 0
    # compute distinct ranks and assign medals similar to _compute_round_medal_map
    distinct = []
    for r in rows:
        rr = r['round_rank']
        if rr not in distinct:
            distinct.append(rr)
    # medal assignments for ranks
    # first three distinct ranks correspond to medals; award season points: 🥇 +4, 🥈 +2, 🥉 +1
    rank_award = {}
    awards = [4,2,1]
    for i, rr in enumerate(distinct[:3]):
        rank_award[rr] = awards[i]
    awarded_total = 0
    awarded_users = 0
    for r in rows:
        award = rank_award.get(r['round_rank'])
        if award:
            # use existing connection to avoid sqlite locked issues
            add_field(group_id, r['user_id'], 'season_pts', award, conn=conn)
            cur.execute("INSERT INTO settlements (group_id, round_id, match_id, user_id, points_awarded, reason, ts) VALUES (?, ?, NULL, ?, ?, ?, ?)",
                         (group_id, round_id, r['user_id'], award, 'round_award', datetime.utcnow().isoformat()))
            awarded_total += award
            awarded_users += 1
    # 清零所有成员的回合积分，为下一回合做准备
    cur.execute("UPDATE members SET round_pts = 0 WHERE group_id = ?", (group_id,))
    # mark round ended
    cur.execute("UPDATE rounds SET status = 'ended', ended_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), round_id))
    conn.commit()
    conn.close()
    recompute_ranks(group_id)
    return awarded_users, awarded_total

# export settlements CSV for current active round
def export_settlements_csv_db(group_id: int) -> str:
    active = get_active_round(group_id)
    if not active:
        raise ValueError("no active round")
    round_id = active['id']
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM settlements WHERE group_id = ? AND round_id = ? ORDER BY ts ASC", (group_id, round_id))
    rows = cur.fetchall()
    conn.close()
    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f'settlements_round_{group_id}_{round_id}_{int(datetime.utcnow().timestamp())}.csv')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['id','group_id','round_id','match_id','user_id','points_awarded','reason','ts'])
        for r in rows:
            writer.writerow([r['id'], r['group_id'], r['round_id'], r['match_id'], r['user_id'], r['points_awarded'], r['reason'], r['ts']])
    return path

# Command handlers
@startround.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    if not is_admin(event):
        await startround.send("只有群主/管理员可以创建回合")
        return
    name = args.extract_plain_text().strip() or f"round_{datetime.utcnow().isoformat()}"
    rid = add_round(event.group_id, name, event.user_id)
    await startround.send(f"已创建回合 {rid} ({name}) 并设为 open")
    return

@addmatch.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    if not is_admin(event):
        await addmatch.send("只有群主/管理员可以添加比赛")
        return
    active = get_active_round(event.group_id)
    if not active:
        await addmatch.send("没有活动回合，请先用 #startround 创建")
        return
    text = args.extract_plain_text().strip()
    # 现在自动分配序号，命令格式：#addmatch TeamA vs TeamB
    try:
        parts = text.split()
        vs_index = parts.index('vs')
        home = ' '.join(parts[:vs_index])
        away = ' '.join(parts[vs_index+1:])
        if not home or not away:
            raise ValueError()
    except Exception:
        await addmatch.send("用法：#addmatch TeamA vs TeamB（序号将自动分配）")
        return
    assigned_idx = add_match_to_round(active['id'], None, home, away)
    await addmatch.send(f"已为回合 {active['id']} 添加比赛：{home} vs {away}（序号 {assigned_idx}）")
    return

@addmatches.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    """一次性添加多场比赛，格式示例：
    #addmatches TeamA vs TeamB; TeamC vs TeamD
    支持用分号或换行分隔多条记录，序号自动分配。"""
    if not is_admin(event):
        await addmatches.send("只有群主/管理员可以添加比赛")
        return
    active = get_active_round(event.group_id)
    if not active:
        await addmatches.send("没有活动回合，请先用 #startround 创建")
        return
    text = args.extract_plain_text().strip()
    if not text:
        await addmatches.send("用法：#addmatches TeamA vs TeamB; TeamC vs TeamD（支持分号或换行分隔）")
        return
    parts = [p.strip() for p in re.split(r'[;\n\r]+', text) if p.strip()]
    added = []
    errors = []
    for p in parts:
        try:
            tok = p.split()
            vs_index = tok.index('vs')
            home = ' '.join(tok[:vs_index])
            away = ' '.join(tok[vs_index+1:])
            if not home or not away:
                raise ValueError()
        except Exception:
            errors.append(p)
            continue
        try:
            idx = add_match_to_round(active['id'], None, home, away)
            added.append((idx, home, away))
        except Exception as e:
            errors.append(f"{p} -> {e}")
    lines = []
    if added:
        for idx, home, away in added:
            lines.append(f"{idx}: {home} vs {away}")
    if errors:
        lines.append("以下条目添加失败：")
        lines.extend(errors)
    if not lines:
        await addmatches.send("未添加任何比赛，请检查格式")
        return
    await addmatches.send("已添加比赛：\n" + "\n".join(lines))
    return

@listmatches.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    active = get_active_round(event.group_id)
    if not active:
        await listmatches.send("当前无活动回合")
        return
    rows = list_matches_of_round(active['id'])
    if not rows:
        await listmatches.send("当前回合无比赛")
        return
    lines = []
    for r in rows:
        res = f"{r['result_home']}-{r['result_away']}" if r['result_home'] is not None and r['result_away'] is not None else "-"
        lines.append(f"{r['idx']}: {r['home']} vs {r['away']} 结果: {res}")
    await listmatches.send("比赛列表：\n" + "\n".join(lines))
    return

@predict.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    active = get_active_round(event.group_id)
    if not active:
        await predict.send("当前无活动回合，无法提交预测")
        return
    text = args.extract_plain_text().strip()
    if not text:
        await predict.send("用法：#predict <序号> [@user] X-Y（管理员可为他人指定 @user）")
        return
    parts = text.split()
    # 支持两种格式：<idx> X-Y 或 <idx> <user> X-Y
    try:
        idx = int(parts[0])
    except Exception:
        await predict.send("用法：#predict <序号> [@user] X-Y（序号应为数字）")
        return
    # 优先从消息中提取 @ 提及
    mention_uid = extract_at_user(event)
    target_user = event.user_id
    score_token = None
    if mention_uid:
        # 有 @ 提及，需要管理员权限才能为他人操作
        if mention_uid != event.user_id and not is_admin(event):
            await predict.send("只有管理员可以为他人提交预测")
            return
        target_user = mention_uid
        # 从 parts 中寻找第一个比分 token
        for tok in parts[1:]:
            if re.search(r"^\d+-\d+$", tok):
                score_token = tok
                break
    elif len(parts) == 2:
        score_token = parts[1]
    elif len(parts) >= 3:
        # parts[1] 可能是用户或 score
        if re.search(r"^\d+-\d+$", parts[1]):
            score_token = parts[1]
        else:
            # 视为用户 ID（纯数字写法，仅管理员可用）
            m = re.search(r"(\d+)", parts[1])
            if m:
                if not is_admin(event):
                    await predict.send("只有管理员可以为他人提交预测")
                    return
                target_user = int(m.group(1))
                score_token = parts[2] if len(parts) >= 3 else None
            else:
                score_token = parts[1] if len(parts) >= 2 else None
    else:
        score_token = parts[1] if len(parts) >= 2 else None
    if not score_token or not re.search(r"^(\d+)-(\d+)$", score_token):
        await predict.send("用法：#predict <序号> [@user] X-Y（示例：#predict 2 1-0 或 #predict 2 @12345678 1-0）")
        return
    ph, pa = score_token.split('-')
    try:
        ph = int(ph); pa = int(pa)
    except Exception:
        await predict.send("比分格式错误，应为 X-Y，X/Y 为整数")
        return
    m = get_match(active['id'], idx)
    if not m:
        await predict.send("未找到该序号的比赛")
        return
    # users can only submit while round open
    if active['status'] != 'open':
        await predict.send("本回合已关闭，不接受预测")
        return
    # upsert prediction for target_user
    upsert_prediction(event.group_id, m['id'], target_user, ph, pa)
    add_member(event.group_id, target_user)
    if target_user == event.user_id:
        await predict.send(f"已记录预测：比赛 {idx} 预计 {ph}-{pa}")
    else:
        try:
            display = await get_display_name(bot, event.group_id, target_user)
        except Exception:
            display = str(target_user)
        await predict.send(f"已为 {display}({target_user}) 记录预测：比赛 {idx} 预计 {ph}-{pa}")
    return

@mypreds.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    """查看自己或指定用户在当前回合的预测。可查看他人：#mypreds 或 #mypreds @12345678"""
    active = get_active_round(event.group_id)
    if not active:
        await mypreds.send("当前无活动回合")
        return
    text = args.extract_plain_text().strip()
    # 优先从消息中提取 @ 提及
    mention_uid = extract_at_user(event)
    target_user = event.user_id
    if mention_uid:
        target_user = mention_uid
    elif text:
        # 尝试解析 user id（任何人都可查看他人）
        m = re.search(r"(\d+)", text)
        if m:
            target_user = int(m.group(1))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT m.idx, m.home, m.away, p.pred_home, p.pred_away, p.awarded_points FROM matches m LEFT JOIN predictions p ON m.id = p.match_id AND p.user_id = ? WHERE m.round_id = ? ORDER BY m.idx ASC", (target_user, active['id']))
    rows = cur.fetchall()
    conn.close()
    lines = []
    for r in rows:
        ph = r['pred_home'] if 'pred_home' in r.keys() and r['pred_home'] is not None else '-'
        pa = r['pred_away'] if 'pred_away' in r.keys() and r['pred_away'] is not None else '-'
        lines.append(f"{r['idx']}: {r['home']} vs {r['away']} 预测 {ph}-{pa} 已得分 {r['awarded_points']}")
    if target_user == event.user_id:
        await mypreds.send("你的预测：\n" + "\n".join(lines))
    else:
        try:
            display = await get_display_name(bot, event.group_id, target_user)
        except Exception:
            display = str(target_user)
        await mypreds.send(f"{display}({target_user}) 的预测：\n" + "\n".join(lines))
    return

# 在 mypreds 之后插入 matchpreds handler
@matchpreds.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    """查看某场比赛的所有玩家预测：#matchpreds <序号>"""
    active = get_active_round(event.group_id)
    if not active:
        await matchpreds.send("当前无活动回合")
        return
    text = args.extract_plain_text().strip()
    if not text:
        await matchpreds.send("用法：#matchpreds <序号>")
        return
    try:
        parts = text.split()
        idx = int(parts[0])
    except Exception:
        await matchpreds.send("用法：#matchpreds <序号>（序号应为数字）")
        return
    m = get_match(active['id'], idx)
    if not m:
        await matchpreds.send("未找到该序号的比赛")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, pred_home, pred_away, awarded_points FROM predictions WHERE match_id = ? ORDER BY awarded_points DESC, user_id ASC", (m['id'],))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await matchpreds.send(f"比赛 {idx}：{m['home']} vs {m['away']} 暂无任何预测记录")
        return
    lines = []
    for r in rows:
        uid = r['user_id']
        try:
            name = await get_display_name(bot, event.group_id, uid)
        except Exception:
            name = str(uid)
        ph = r['pred_home'] if r['pred_home'] is not None else '-'
        pa = r['pred_away'] if r['pred_away'] is not None else '-'
        pts = r['awarded_points'] if r['awarded_points'] is not None else 0
        lines.append(f"{name}({uid}): {ph}-{pa} 已得分 {pts}")
    await matchpreds.send(f"比赛 {idx}: {m['home']} vs {m['away']} 的预测：\n" + "\n".join(lines))
    return

@setmatchresult.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    if not is_admin(event):
        await setmatchresult.send("只有群主/管理员可以设置比赛结果")
        return
    active = get_active_round(event.group_id)
    if not active:
        await setmatchresult.send("当前无活动回合")
        return
    text = args.extract_plain_text().strip()
    try:
        parts = text.split()
        idx = int(parts[0])
        score = parts[1]
        ah, aa = score.split('-')
        ah = int(ah); aa = int(aa)
    except Exception:
        await setmatchresult.send("用法：#setmatchresult <序号> X-Y")
        return
    try:
        suc, fail = set_match_result_db(event.group_id, idx, ah, aa, event.user_id)
    except Exception as e:
        await setmatchresult.send(f"设置失败：{e}")
        return
    # do one batch refresh
    await refresh_cardname(bot, event.group_id)
    await setmatchresult.send(f"已设置比赛 {idx} 结果为 {ah}-{aa}，处理预测 {suc} 成功，{fail} 失败")
    return

@endround.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if not is_admin(event):
        await endround.send("只有群主/管理员可以结束回合")
        return
    active = get_active_round(event.group_id)
    if not active:
        await endround.send("当前无活动回合")
        return
    awarded_users, awarded_total = end_round_db(event.group_id, event.user_id)
    # batch refresh
    await refresh_cardname(bot, event.group_id)
    # 查询本回合的 round_award 发放记录，列出冠亚季军
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, points_awarded FROM settlements WHERE group_id = ? AND round_id = ? AND reason = 'round_award' ORDER BY points_awarded DESC", (event.group_id, active['id']))
    rows = cur.fetchall()
    conn.close()
    medals_map = {4: '🥇', 2: '🥈', 1: '🥉'}
    parts = []
    for award in [4, 2, 1]:
        users = [r['user_id'] for r in rows if r['points_awarded'] == award]
        if users:
            names = []
            for uid in users:
                names.append(await get_display_name(bot, event.group_id, uid))
            parts.append(f"{medals_map[award]}: {', '.join(names)} (+{award} pts)")
    medal_text = "\n".join(parts) if parts else "无"
    await endround.send(f"回合已结束。发放奖励用户数：{awarded_users}，总积分：{awarded_total}\n得分结果：\n{medal_text}")
    return

@abortround.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if not is_admin(event):
        await abortround.send("只有群主/管理员可以中止回合")
        return
    active = get_active_round(event.group_id)
    if not active:
        await abortround.send("当前无活动回合")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE rounds SET status = 'aborted', ended_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), active['id']))
    conn.commit()
    conn.close()
    await abortround.send("回合已中止，不会发放奖励")
    return

@exportsettlements.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if not is_admin(event):
        await exportsettlements.send("只有群主/管理员可以导出结算记录")
        return
    try:
        path = export_settlements_csv_db(event.group_id)
    except Exception as e:
        await exportsettlements.send(f"导出失败：{e}")
        return
    await exportsettlements.send(f"已导出结算记录：{path}")
    return

@setpred.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    """管理员用：#setpred <序号> <user_id|@qq> X-Y
    将指定用户对指定序号比赛的预测改为 X-Y，若比赛已记录赛果则重新结算并更新回合积分。"""
    if not is_admin(event):
        await setpred.send("只有群主/管理员可以修改他人预测")
        return
    active = get_active_round(event.group_id)
    if not active:
        await setpred.send("当前无活动回合")
        return
    text = args.extract_plain_text().strip()
    parts = text.split()
    # 优先从消息中提取 @ 提及
    mention_uid = extract_at_user(event)
    user_id = None
    idx = None
    ph = None
    pa = None
    if mention_uid:
        # 有 @ 提及，格式应为：#setpred <序号> @user X-Y
        # parts 中应该只有 <序号> 和 X-Y（@ 段被 extract_plain_text 去掉）
        if len(parts) < 2:
            await setpred.send("用法：#setpred <序号> @user X-Y")
            return
        try:
            idx = int(parts[0])
            score = parts[1]
            ph, pa = score.split('-')
            ph = int(ph); pa = int(pa)
        except Exception:
            await setpred.send("用法：#setpred <序号> @user X-Y")
            return
        user_id = mention_uid
    else:
        # 无 @ 提及，格式应为：#setpred <序号> <user_id> X-Y
        if len(parts) < 3:
            await setpred.send("用法：#setpred <序号> <user_id|@qq> X-Y")
            return
        try:
            idx = int(parts[0])
            user_s = parts[1]
            score = parts[2]
            ph, pa = score.split('-')
            ph = int(ph); pa = int(pa)
        except Exception:
            await setpred.send("用法：#setpred <序号> <user_id|@qq> X-Y")
            return
        # parse user id from user_s
        m = re.search(r"(\d+)", user_s)
        if not m:
            await setpred.send("无法解析用户 ID，请填写数字 QQ 号或 @ 提及")
            return
        user_id = int(m.group(1))
    round_id = active['id']
    mrec = get_match(round_id, idx)
    if not mrec:
        await setpred.send("未找到该序号的比赛")
        return
    conn = get_conn()
    cur = conn.cursor()
    # 查找已有预测
    cur.execute("SELECT * FROM predictions WHERE match_id = ? AND user_id = ?", (mrec['id'], user_id))
    prow = cur.fetchone()
    now = datetime.utcnow().isoformat()
    if prow:
        cur.execute("UPDATE predictions SET pred_home = ?, pred_away = ?, updated_at = ? WHERE id = ?", (ph, pa, now, prow['id']))
    else:
        cur.execute("INSERT INTO predictions (match_id, group_id, user_id, pred_home, pred_away, awarded_points, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (mrec['id'], event.group_id, user_id, ph, pa, now))
    # 如果该比赛已设置实际赛果，重新结算该用户得分并更新 round_pts
    if mrec['result_home'] is not None and mrec['result_away'] is not None:
        # 获取之前的 awarded_points
        prev_awarded = prow['awarded_points'] if prow and prow['awarded_points'] is not None else 0
        adj_actual_h = (mrec['result_home'] or 0)
        adj_actual_a = (mrec['result_away'] or 0)
        points = compare_prediction(adj_actual_h, adj_actual_a, ph, pa)
        delta = points - prev_awarded
        if delta != 0:
            add_field(event.group_id, user_id, 'round_pts', delta, conn=conn)
            add_field(event.group_id, user_id, 'round_pred_pts', delta, conn=conn)
            cur.execute("INSERT INTO settlements (group_id, round_id, match_id, user_id, points_awarded, reason, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (event.group_id, round_id, mrec['id'], user_id, delta, 'admin_setpred', datetime.utcnow().isoformat()))
        cur.execute("UPDATE predictions SET awarded_points = ?, updated_at = ? WHERE match_id = ? AND user_id = ?", (points, now, mrec['id'], user_id))
    conn.commit()
    conn.close()
    # 更新排行并刷新名片
    recompute_ranks(event.group_id)
    await refresh_cardname(bot, event.group_id)
    await setpred.send(f"已将用户 {user_id} 的比赛 {idx} 预测修改为 {ph}-{pa} 并更新回合积分")
    return

@predicts.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    """按回合内比赛序号顺序一次性提交多场预测，省去 idx。
    示例：#predicts 1-0 2-1 0-0 或 #predicts @12345678 1-0 2-1
    支持空格、逗号、分号或换行分隔。管理员可指定第一个参数为 @user 来为他人提交预测。"""
    active = get_active_round(event.group_id)
    if not active:
        await predicts.send("当前无活动回合，无法提交预测")
        return
    if active['status'] != 'open':
        await predicts.send("本回合已关闭，不接受预测")
        return
    text = args.extract_plain_text().strip()
    if not text:
        await predicts.send("用法：#predicts [@user] X-Y [X-Y ...] ，按当前回合比赛顺序对应每场预测，省去序号")
        return
    # 宽松分隔
    tokens = [t.strip() for t in re.split(r'[,;\s]+', text) if t.strip()]
    if not tokens:
        await predicts.send("未识别到有效输入")
        return
    # 优先从消息中提取 @ 提及
    mention_uid = extract_at_user(event)
    target_user = event.user_id
    scores_tokens = tokens[:]
    if mention_uid:
        # 有 @ 提及，需要管理员权限才能为他人操作
        if mention_uid != event.user_id and not is_admin(event):
            await predicts.send("只有管理员可以为他人提交批量预测")
            return
        target_user = mention_uid
        # 不需要从 tokens 中去除 @xxx，因为 extract_plain_text 已经去掉了 @ 段
        # 但如果第一个 token 恰好是纯数字（与提及重复），则跳过
        if tokens and re.match(r'^@?\d+$', tokens[0]):
            scores_tokens = tokens[1:]
    else:
        # 兼容纯数字 id 写法（例如：#predicts 12345678 1-0 ...）
        first = tokens[0]
        m_user = re.match(r'^(?:@)?(\d+)$', first)
        if m_user:
            # first token 是用户 id
            if not is_admin(event):
                await predicts.send("只有管理员可以为他人提交批量预测")
                return
            target_user = int(m_user.group(1))
            scores_tokens = tokens[1:]
            if not scores_tokens:
                await predicts.send("请在用户后面提供比分列表，例如：#predicts @12345678 1-0 2-1")
                return
    # 从 scores_tokens 中提取所有 score 值
    scores = []
    for t in scores_tokens:
        m = re.match(r'^(\d+)-(\d+)$', t)
        if m:
            scores.append((int(m.group(1)), int(m.group(2))))
    if not scores:
        await predicts.send("未识别到有效的比分，示例：#predicts 1-0 2-1 0-0")
        return
    matches = list_matches_of_round(active['id'])
    if not matches:
        await predicts.send("当前回合无比赛，无法预测")
        return
    n = min(len(scores), len(matches))
    recorded = []
    for i in range(n):
        m = matches[i]
        ph, pa = scores[i]
        # 仅对目标用户写入预测
        upsert_prediction(event.group_id, m['id'], target_user, ph, pa)
        add_member(event.group_id, target_user)
        recorded.append(f"{m['idx']}: {ph}-{pa}")
    if not recorded:
        await predicts.send("未记录任何预测（可能预测数超过比赛数或格式错误）")
        return
    if target_user == event.user_id:
        await predicts.send("已记录预测：\n" + "\n".join(recorded))
    else:
        try:
            display = await get_display_name(bot, event.group_id, target_user)
        except Exception:
            display = str(target_user)
        await predicts.send(f"已为 {display}({target_user}) 记录预测（不会修改你的预测）：\n" + "\n".join(recorded))
    return
