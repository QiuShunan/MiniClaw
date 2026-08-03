from dotenv import load_dotenv
load_dotenv(override=True)

import asyncio
import os
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage
import botpy 
from botpy.message import C2CMessage



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


class MiniClawQQBot(botpy.Client):

    async def on_c2c_message_create(self, message: C2CMessage):
        """用户给机器人发私聊消息时触发"""
        user_message = message.content.strip()
        user_id = message.author.user_openid

        print(f"[收到消息] 用户 {user_id}: {user_message}")
        response = await chat(user_message)

        print(f"[AI回复] {response}")

        await message.reply(content=response)

# async def main():
#     response = await chat("查看项目有哪些文件")
#     print(response)


def main():
    intents = botpy.Intents(public_messages=True)
    client = MiniClawQQBot(intents=intents)
    client.run(
        appid=os.getenv("QQ_APP_ID"),
        secret=os.getenv("QQ_APP_SECRET")
    )