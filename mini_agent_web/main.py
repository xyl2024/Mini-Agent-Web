"""FastAPI 应用入口。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mini_agent_web.database import init_db
from mini_agent_web.routers import agent

# 创建 FastAPI 应用
app = FastAPI(
    title="Mini Agent Web",
    description="Mini Agent Web API - 将 CLI 程序的交互体验带到 Web 浏览器上",
    version="0.1.0",
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)


@app.on_event("startup")
async def startup_event():
    """启动时初始化数据库。"""
    init_db()


@app.get("/")
async def root():
    """根路由。"""
    return {
        "message": "Mini Agent Web API",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """健康检查。"""
    return {"status": "healthy"}


# 注册路由
app.include_router(agent.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
