import os
import json
import asyncio
from datetime import datetime
import botpy
from botpy.message import C2CMessage
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage
load_dotenv(override=True)

feishu_client = lark.Client.builder() \
    .app_id(os.getenv("FEISHU_APP_ID")) \
    .app_secret(os.getenv("FEISHU_APP_SECRET")) \
    .build()


# Claude Agent
async def chat(message: str) -> str:
    """发送消息给 Claude Agent，返回完整回复。"""
    response_parts = []
    try:
        async for msg in query(
            prompt=message,
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Edit", "Glob"],  # 自动批准这些工具
                permission_mode="acceptEdits",  # 自动批准文件编辑
            ),
        ):
            # 打印人类可读的输出
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if hasattr(block, "text"):
                        response_parts.append(block.text)
                    elif hasattr(block, "name"):
                        response_parts.append(f"[调用工具: {block.name}]")
            elif isinstance(msg, ResultMessage):
                if msg.subtype != "success":
                    response_parts.append(f"[任务结束：{msg.subtype}]")
    except Exception as e:
        return f"与 Claude Agent 通信时出错：{e}"
    return "\n".join(response_parts)


# ============ 统一发送入口（定时任务触发时用）============
async def send_to_user(platform: str, user_id: str, content: str) -> None:
    """定时任务触发时，把结果主动发回给设置任务的用户。

    飞书：直接调用 API，没有频次限制。
    QQ：平台对机器人主动发消息有严格审核，需额外申请权限，暂时只打印日志。
    """
    if platform == "feishu":
        await send_feishu_message(user_id, content)
    elif platform == "qq":
        print(f"[QQ 待实现] 应发送给 {user_id}: {content}")
    else:
        print(f"[未知平台] {platform}: {content}")


# scheduler
scheduler = AsyncIOScheduler()

scheduled_tasks = {}  # task_id -> {cron, message, platform, user_id, created_at}
# Instruction 
async def parse_command(message: str, platform: str, user_id: str) -> str:
    """所有平台消息的统一入口。判断是指令还是普通聊天，返回回复内容。"""
    message = message.strip()

    if message == "/help":
        return (
            "可用指令：\n"
            "/task add <cron> <内容>  添加定时任务\n"
            "  例如：/task add 0 9 * * * 提醒我查看邮件\n"
            "/task list               列出所有任务\n"
            "/task remove <task_id>   删除任务\n"
            "/help                    显示此帮助\n"
            "其他消息                  与 AI 对话"
        )

    if message.startswith("/task"):
        # maxsplit=3 最多切成 4 段，足够拿到子命令，避免过度切分
        # "/task add 0 9 * * * 提醒我" → ["/task", "add", "0", "9 * * * 提醒我"]
        parts = message.split(maxsplit=3)
        if len(parts) < 2:
            return "用法：/task add/list/remove ..."

        sub_cmd = parts[1]

        if sub_cmd == "list":
            if not scheduled_tasks:
                return "当前没有定时任务"
            lines = ["定时任务列表："]
            for task_id, info in scheduled_tasks.items():
                lines.append(f"[{task_id}] {info['cron']} - {info['message'][:20]}...")
            return '\n'.join(lines)

        elif sub_cmd == "add":
            # 把整条消息按空格全部拆开
            # "/task add 0 9 * * * 提醒我查看邮件"
            # → ["/task", "add", "0", "9", "*", "*", "*", "提醒我查看邮件"]
            all_parts = message.split()

            # 去掉前两个词 "/task" 和 "add"，剩下是 cron + 内容
            # → ["0", "9", "*", "*", "*", "提醒我查看邮件"]
            remaining = all_parts[2:]

            # cron 固定 5 段 + 至少 1 个字的内容
            if len(remaining) < 6:
                return "用法：/task add <cron> <内容>\n例如：/task add 0 9 * * * 提醒我查看邮件"

            # remaining[:5] → ["0", "9", "*", "*", "*"] → 拼回 "0 9 * * *"
            cron_expr = " ".join(remaining[:5])
            # remaining[5:] → ["提醒我查看邮件"] → 拼回 "提醒我查看邮件"
            task_message = " ".join(remaining[5:])

            # 用时间戳生成唯一的任务 ID
            task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            try:
                trigger = CronTrigger.from_crontab(cron_expr)
            except Exception:
                return f"Cron 表达式格式错误：{cron_expr}\n例如每天9点：0 9 * * *"

            # execute_task 在 parse_command 内部定义，通过闭包持有外层的
            # platform、user_id、task_message、task_id。
            # parse_command 返回后这些变量依然存活，
            # 几天后定时任务触发时依然可以访问到它们。
            async def execute_task():
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[定时任务] {timestamp} 执行：{task_id}")
                response = await chat(task_message)
                await send_to_user(platform, user_id, f"[定时任务结果]\n{response}")

            # 调度器已在 main() 里启动，直接动态添加新任务即可
            scheduler.add_job(execute_task, trigger, id=task_id)
            # 同步写入业务记录
            scheduled_tasks[task_id] = {
                "cron": cron_expr,
                "message": task_message,
                "platform": platform,
                "user_id": user_id,
                "created_at": datetime.now().isoformat()
            }

            next_run = trigger.get_next_fire_time(None, datetime.now())
            return f"任务 [{task_id}] 添加成功\n下次执行时间：{next_run}"

        elif sub_cmd == "remove":
            if len(parts) < 3:
                return "用法：/task remove <task_id>"
            task_id = parts[2]
            if task_id not in scheduled_tasks:
                return f"任务 [{task_id}] 不存在"
            # 同时从调度器和字典里删除，两者各自维护状态，缺一不可
            scheduler.remove_job(task_id)
            del scheduled_tasks[task_id]
            return f"任务 [{task_id}] 已删除"

        else:
            return f"未知子命令：{sub_cmd}，输入 /help 查看帮助"

    # 不是指令，交给 AI
    return await chat(message)


# QQ Bot
class MiniClawQQBot(botpy.Client):
    async def on_c2c_message_create(self, message: C2CMessage):
        user_message = message.content.strip()
        user_id = message.author.user_openid
        print(f"[收到 QQ 消息] 用户 {user_id}: {user_message}")
        response = await parse_command(user_message, "qq", user_id)
        print(f"[QQ 回复] {response}")
        await message.reply(content=response)



# Feishu Bot

async def send_feishu_message(chat_id: str, content: str) -> None:
    """发送消息到飞书（异步版本）"""
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": content}))
            .build()
        )
        .build()
    )
    resp = await feishu_client.im.v1.message.acreate(request)
    if not resp.success():
        print(f"[飞书错误] 发送消息失败: {resp.msg}")


async def handle_feishu_message(chat_id: str, user_msg: str):
    response = await parse_command(user_msg, "feishu", chat_id)
    print(f"[飞书回复] {response}")
    await send_feishu_message(chat_id, response)


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    if data.event.message.message_type != "text":
        return
    user_message = json.loads(data.event.message.content)["text"]
    chat_id = data.event.message.chat_id
    print(f"[收到飞书消息] {chat_id}: {user_message}")

    loop = asyncio.get_running_loop() #返回当前线程里正在运行的事件循环
    loop.create_task(handle_feishu_message(chat_id, user_message))


async def async_main():
    scheduler.start()
    print("定时任务调度器已启动")

    feishu_event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
        .build()
    )
    feishu_cli = lark.ws.Client(
        os.getenv("FEISHU_APP_ID"),
        os.getenv("FEISHU_APP_SECRET"),
        event_handler=feishu_event_handler,
        log_level=lark.LogLevel.DEBUG,
    )

    qq_intents = botpy.Intents(public_messages=True)
    qq_client = MiniClawQQBot(intents=qq_intents)

    print("正在启动两个平台...")
    await asyncio.gather(
        qq_client.start(
            appid=os.getenv("QQ_APP_ID"),
            secret=os.getenv("QQ_APP_SECRET"),
        ),
        asyncio.to_thread(feishu_cli.start),
    )

def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n正在退出...")
if __name__ == "__main__":
    main()