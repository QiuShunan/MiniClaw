import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage
from dotenv import load_dotenv

load_dotenv(override=True)

async def main():
    # Agentic loop: streams messages as Claude works
    async for message in query(
        prompt="Review utils.py for bugs that would cause crashes. Fix any issues you find.",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Glob"],  # 自动批准这些工具
            permission_mode="acceptEdits",  # 自动批准文件编辑
        ),
    ):
        # 打印人类可读的输出
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)  # Claude 的推理
                elif hasattr(block, "name"):
                    print(f"Tool: {block.name}")  # 正在调用的工具
        elif isinstance(message, ResultMessage):
            print(f"Done: {message.subtype}")  # 最终结果


asyncio.run(main())