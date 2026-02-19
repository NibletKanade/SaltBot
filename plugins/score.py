from nonebot import on_command ,on_message
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from typing import Optional, List, Tuple

# Database and helpers moved to plugins/_common.py
from ._common import (
    get_conn,
    add_member,
    remove_member,
    set_field,
    add_field,
    modify_and_refresh,
    get_member,
    get_all_members,
    recompute_ranks,
    _bracket_re,
    get_display_name,
    _compute_round_medal_map,
    refresh_cardname,
)

ping = on_command("ping", priority=5)
# setcard = on_command("改名", priority=5)

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
# 精简帮助命令
h_cmd = on_command("h", priority=10)
# 新增排行榜命令
seasonboard = on_command("seasonboard", priority=10)
roundboard = on_command("roundboard", priority=10)

# 当机器人被 @ 时自动回复
mention_reply = on_message(rule=to_me(), priority=15)

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
    r, suc, fail = await modify_and_refresh(bot, event.group_id, user_id, 'season_pts', set=val)
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
    r, suc, fail = await modify_and_refresh(bot, event.group_id, user_id, 'season_pts', add=delta)
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
    r, suc, fail = await modify_and_refresh(bot, event.group_id, user_id, 'round_pts', set=val)
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
    r, suc, fail = await modify_and_refresh(bot, event.group_id, user_id, 'round_pts', add=delta)
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
        "--- 数据系统指令 ---\n"
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
        "\n"
        "--- 竞猜回合系统指令 ---\n"
        "#startround [名字] — 管理员：创建并开启新回合，若未给名则自动命名\n"
        "#addmatch TeamA vs TeamB — 管理员：为当前回合添加比赛，序号自动分配并返回\n"
        "#addmatches A vs B; C vs D — 管理员：一次性添加多场，支持分号或换行分隔\n"
        "#listmatches — 列出当前回合所有比赛及已录赛果\n"
        "#predict <序号> [@user] X-Y — 提交/更新单场预测；若指定 @user 需管理员权限（默认提交自己）\n"
        "#predicts [@user] X-Y [X-Y ...] — 批量预测，按回合内比赛顺序对应比分；指定 @user 仅限管理员（默认自己）\n"
        "#mypreds [@user] — 查看自己或指定用户在当前回合的所有预测（任何人可查看他人）\n"
        "#matchpreds <序号> — 列出所有用户对指定比赛的预测和积分\n"
        "#setmatchresult <序号> X-Y — 管理员：设置比赛赛果并结算该场所有预测\n"
        "#setpred <序号> <user_id|@qq> X-Y — 管理员：为指定用户修改某场预测，若已录赛果则重新结算并更新积分\n"
        "#endround — 管理员：结束回合并按回合名次发放赛季积分（🥇+4, 🥈+2, 🥉+1），同时清零回合分数\n"
        "#abortround — 管理员：中止回合（不发放奖励）\n"
    )
    await help_cmd.send(msg)
    return

@h_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """精简帮助，仅列出常用指令。"""
    msg = (
        "常用指令速查：\n"
        "#register @user — 注册用户（不带 @ 则注册自己）\n"
        "#addSpts @user [数量] — 增加赛季分数，默认 1\n"
        "#addRpts @user [数量] — 增加回合分数，默认 1\n"
        "#addlibido @user [数量] — 增加 Libido，默认 1\n"
        "#addrc @user [数量] — 增加红牌 RC，默认 1\n"
        "#addyc @user [数量] — 增加黄牌 YC，默认 1\n"
        "#showall — 列出本群所有已注册群友的数据\n"
        "#show @user — 显示特定群友的数据（不带 @ 则显示自己）\n"
        "#seasonboard — 显示赛季积分榜\n"
        "#roundboard — 显示回合积分榜\n"
        "#help — 查看完整帮助\n"
        "#predicts [@user] X-Y [X-Y ...] — 批量预测比分（不带 @ 操作自己）\n"
        "#mypreds [@user] — 查看自己或指定用户在当前回合的预测\n"
        "#matchpreds <序号> — 列出所有用户对指定比赛的预测和积分\n"
    )
    await h_cmd.send(msg)
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
    cur.execute("SELECT user_id, round_pts, round_rank, round_pred_pts FROM members WHERE group_id = ? ORDER BY round_rank ASC", (event.group_id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await roundboard.send("当前没有注册的用户")
        return
    lines = []
    for r in rows:
        display = await get_display_name(bot, event.group_id, r['user_id'])
        medal = medal_map.get(r['user_id'], '')
        round_pts = r['round_pts'] or 0
        pred_total = r['round_pred_pts'] or 0
        tool_pts = round_pts - pred_total
        lines.append(
            f"{display}: R-pts={round_pts} (Rank {r['round_rank']}) {medal} 预测={pred_total} 道具={tool_pts}"
        )
    await roundboard.send("回合积分榜：\n" + "\n".join(lines))
    return

@mention_reply.handle()
async def _(event: GroupMessageEvent):
    await mention_reply.finish("白白是爸爸，盐盐是妈妈！(#^.^#)")