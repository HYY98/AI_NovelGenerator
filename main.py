import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.db.mongo import connect_to_mongo, close_mongo_connection
from backend.db.indexes import init_all_indexes
from backend.api.default_routers.config_router import router as config_router
from backend.api.default_routers.novel_router import router as novel_router
from backend.api.default_routers.upload_router import router as upload_router
from backend.api.default_routers.volume_router import router as volume_router
from backend.api.default_routers.faction_router import router as faction_router
from backend.api.default_routers.faction_relation_router import router as faction_relation_router
from backend.api.default_routers.character_router import router as character_router
from backend.api.default_routers.character_relation_router import router as character_relation_router
from backend.api.default_routers.setting_card_router import router as setting_card_router
from backend.api.default_routers.chapter_router import router as chapter_router
from backend.api.default_routers.character_faction_binding_router import (
    router as character_faction_binding_router,
)
from backend.api.llm_routers.create_novel_router import router as create_novel_router
from backend.api.llm_routers.character_generation_router import router as character_generation_router
from backend.api.llm_routers.chapter_generation_router import router as chapter_generation_router
from backend.api.llm_routers.setting_card_generation_router import (
    router as setting_card_generation_router,
)
from backend.api.llm_routers.novel_blueprint_router import router as novel_blueprint_router
from backend.api.default_routers.setting_card_accept_router import (
    router as setting_card_accept_router,
)
from backend.llm.prompts.prompt_selector import check_extended_prompts, load_prompt_config
from backend.runtime import (
    apply_runtime_flags_from_argv,
    build_uvicorn_log_config,
    get_backend_port,
    get_backend_log_level,
    get_frontend_port,
    is_backend_debug_enabled,
)

apply_runtime_flags_from_argv()

logger = logging.getLogger(__name__)
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = get_backend_port()
FRONTEND_PORT = get_frontend_port()
FRONTEND_ORIGINS = [
    f"http://127.0.0.1:{FRONTEND_PORT}",
    f"http://localhost:{FRONTEND_PORT}",
]


# FastAPI setup with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI应用的生命周期管理，在启动时完成配置校验与资源连接。

    Args:
        app: 当前 FastAPI 应用实例。

    Returns:
        异步生命周期上下文生成器。
    """
    logger.info(
        "Backend startup: debug=%s docs=http://%s:%s/docs",
        is_backend_debug_enabled(),
        BACKEND_HOST,
        BACKEND_PORT,
    )
    # 启动早期读取一次提示词配置，让自定义 prompt.yaml 的错误能立刻出现在控制台日志中。
    load_prompt_config(force_reload=True)
    # 章节/设定卡 AI 的提示词分组只告警不阻断启动：缺失项会回退默认提示词。
    for problem in check_extended_prompts(force_reload=True):
        logger.warning("章节/设定卡 AI 提示词检查: %s", problem)
    # Setup Mongo
    await connect_to_mongo()
    # Initialize DB Indexes
    await init_all_indexes()
    logger.info("Backend startup completed.")
    yield
    # Teardown
    await close_mongo_connection()
    logger.info("Backend shutdown completed.")

app = FastAPI(title="Novel Generator API", lifespan=lifespan, debug=is_backend_debug_enabled())


def _sanitize_validation_detail(value):
    """递归清洗校验错误，避免孤立 Unicode surrogate 让 422 序列化再次失败。

    Args:
        value: FastAPI/Pydantic 产生的任意校验错误节点。

    Returns:
        仅含可安全 UTF-8 JSON 编码标量、列表和字典的结构。
    """
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, dict):
        return {
            _sanitize_validation_detail(str(key)): (
                "<invalid input>"
                if key == "input"
                else _sanitize_validation_detail(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_validation_detail(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_validation_detail(str(value))


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """以不回显原始输入的安全 JSON 返回请求校验错误。

    Args:
        _request: 当前 HTTP 请求；保留参数以符合 FastAPI handler 协议。
        exc: FastAPI 聚合的请求校验异常。

    Returns:
        UTF-8 可编码的 422 JSON 响应。
    """
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_validation_detail(exc.errors())},
    )

# CORS 与启动器选定的前端端口保持一致，同时兼容 localhost 与 IPv4 回环地址。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
os.makedirs("static/covers", exist_ok=True)
app.mount("/static/covers", StaticFiles(directory="static/covers"), name="static_covers")

app.include_router(novel_router)
app.include_router(volume_router)
app.include_router(faction_router)
app.include_router(faction_relation_router)
app.include_router(character_router)
app.include_router(character_relation_router)
app.include_router(character_faction_binding_router)
app.include_router(setting_card_router)
app.include_router(chapter_router)
app.include_router(config_router)
app.include_router(create_novel_router)
app.include_router(character_generation_router)
app.include_router(chapter_generation_router)
app.include_router(setting_card_generation_router)
app.include_router(novel_blueprint_router)
app.include_router(setting_card_accept_router)
app.include_router(upload_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=BACKEND_HOST,
        port=BACKEND_PORT,
        reload=False,
        log_config=build_uvicorn_log_config(),
        log_level=get_backend_log_level().lower(),
        reload_excludes=[
            "frontend/**",
            "frontend/.next/**",
            "frontend/node_modules/**",
        ],
    )
