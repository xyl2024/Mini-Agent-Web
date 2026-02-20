"""Web 测试脚本 - 用于测试 Mini Agent Web API。"""

"""
使用方法

  1. 先安装测试依赖

  pip install websockets aiohttp
  # 或
  uv pip install websockets aiohttp

  2. 启动服务器（开一个终端）

  uv run uvicorn mini_agent_web.main:app --reload

  3. 运行测试脚本（另一个终端）

  uv run python test_web.py

  流程解释

  ┌─────────────────────────────────────────────────────────┐
  │  1. 先建立 WebSocket 连接 (ws://localhost:8000/agent/ws?session_id=xxx)  │
  └─────────────────────────────────────────────────────────┘
                                ↓
  ┌─────────────────────────────────────────────────────────┐
  │  2. 调用 POST /agent/chat，传入相同的 session_id          │
  │     (服务器会在后台启动 Agent，并通过 WebSocket 推送)      │
  └─────────────────────────────────────────────────────────┘
                                ↓
  ┌─────────────────────────────────────────────────────────┐
  │  3. WebSocket 会依次收到:                                │
  │     - thinking (思考)                                   │
  │     - message (助手回复)                                │
  │     - tool_call (工具调用)                               │
  │     - tool_result (工具结果)                            │
  │     - done (完成)                                       │
  └─────────────────────────────────────────────────────────┘

  获取历史消息

  如果 Agent 已经运行完成，可以用：

  # 查看某个 session 的历史
  uv run python test_web.py --history <session_id>

  # 或者直接用 curl
  curl "http://localhost:8000/agent/chat?session_id=xxx"
"""
import asyncio
import json
import uuid
from pathlib import Path

import aiohttp
import websockets


async def test_chat_with_websocket():
    """测试聊天功能：先建立 WebSocket 连接，再发送消息。"""
    session_id = str(uuid.uuid4())
    base_url = "http://localhost:8000"
    ws_url = f"ws://localhost:8000/agent/ws?session_id={session_id}"

    print(f"Session ID: {session_id}")
    print(f"WebSocket URL: {ws_url}")
    print("-" * 50)

    async with websockets.connect(ws_url) as ws:
        # 发送聊天请求
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/agent/chat",
                json={"message": "查询广州番禺区今日的天气", "session_id": session_id},
            ) as resp:
                result = await resp.json()
                print(f"Chat response: {json.dumps(result, ensure_ascii=False)}")

        print("-" * 50)
        print("Waiting for WebSocket messages...")

        # 接收 WebSocket 消息
        message_count = 0
        try:
            while message_count < 200:  # 最多接收 200 条消息
                msg = await asyncio.wait_for(ws.recv(), timeout=600.0)  # 10 分钟超时
                message_count += 1
                data = json.loads(msg)
                msg_type = data.get("type")

                print(f"\n[{message_count}] Type: {msg_type}")

                if msg_type == "thinking":
                    print(f"  Content: {data.get('content', '')}")
                elif msg_type == "message":
                    print(f"  Content: {data.get('content', '')}")
                elif msg_type == "tool_call":
                    print(f"  Tool: {data.get('tool')}")
                    print(f"  Args: {json.dumps(data.get('arguments', {}), ensure_ascii=False)}")
                elif msg_type == "tool_result":
                    content = data.get("content", "")
                    print(f"  Success: {data.get('success')}")
                    print(f"  Content: {content}")
                elif msg_type == "done":
                    print(f"  Final content: {data.get('content', '')}")
                    break
                elif msg_type == "error":
                    print(f"  Error: {data.get('message')}")
                    break

        except asyncio.TimeoutError:
            print("\nTimeout waiting for messages")
        except websockets.exceptions.ConnectionClosed:
            print("\nWebSocket connection closed")

    print("-" * 50)
    print("Test completed!")


async def get_history(session_id: str):
    """获取聊天历史。"""
    base_url = "http://localhost:8000"

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{base_url}/agent/chat?session_id={session_id}"
        ) as resp:
            result = await resp.json()
            print(f"History: {json.dumps(result, ensure_ascii=False, indent=2)}")


async def main():
    """主函数。"""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--history":
        # 获取历史模式
        if len(sys.argv) > 2:
            session_id = sys.argv[2]
        else:
            session_id = input("Enter session_id: ")
        await get_history(session_id)
    else:
        # 正常测试模式
        await test_chat_with_websocket()


if __name__ == "__main__":
    asyncio.run(main())
