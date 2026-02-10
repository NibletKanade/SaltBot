from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.params import CommandArg

ping = on_command("ping", priority=5)
setcard = on_command("改名", priority=5)

@ping.handle()
async def _(event: MessageEvent):
    await ping.finish("pong")

@setcard.handle()
async def _(bot: Bot, event: GroupMessageEvent, args=CommandArg()):
    # 取 at 的用户
    if not event.message:
        await setcard.finish("用法：/改名 @某人 新昵称")

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