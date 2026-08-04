from dotenv import load_dotenv
load_dotenv(override=True)

import asyncio
import os
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage
import botpy 
from botpy.message import C2CMessage
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
import json
import inspect

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


# QQ Bot
class MiniClawQQBot(botpy.Client):

    async def on_c2c_message_create(self, message: C2CMessage):
        """用户给机器人发私聊消息时触发"""
        user_message = message.content.strip()
        user_id = message.author.user_openid

        print(f"[收到消息] 用户 {user_id}: {user_message}")
        response = await chat(user_message)

        print(f"[AI回复] {response}")

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
    response = await chat(user_msg)
    print(f"[AI 回复] {response}")
    await send_feishu_message(chat_id, response)


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    if data.event.message.message_type != "text":
        return
    user_message = json.loads(data.event.message.content)["text"]
    chat_id = data.event.message.chat_id
    print(f"[收到飞书消息] {chat_id}: {user_message}")

    loop = asyncio.get_running_loop() #返回当前线程里正在运行的事件循环
    loop.create_task(handle_feishu_message(chat_id, user_message))


async def main():
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

    print("正在启动 QQ 和飞书两个平台...")
    await asyncio.gather(
        qq_client.start(
            appid=os.getenv("QQ_APP_ID"),
            secret=os.getenv("QQ_APP_SECRET"),
        ),
        asyncio.to_thread(feishu_cli.start),
    )
    
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n正在退出...")
        os._exit(0)