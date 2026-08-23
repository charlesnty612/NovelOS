"""Agents REST 路由（Sprint 3）。

挂在 ``/api`` 前缀下，由 ``packages/core/api/main.py`` 的 ``discover_routers()`` 自动发现。

端点：
- ``POST /agents/sync`` ——从 ``docs/agents/prompts`` 同步所有 prompt 到 agents/prompts 表。
- ``GET /agents`` ——列出已注册的 agents。
- ``GET /agents/{name}/prompts`` ——列出指定 agent 的所有 prompts（按 version 降序）。
- ``POST /agents/{name}/run`` ——执行一次 agent 调用（手工触发 / 测试通道）。

错误码映射：
- 422：请求体缺字段或 mock_script 形态非法。
- 404：prompt / agent 不存在。
- 502：LLM 输出经 1 次重试仍不合规（``AgentOutputError``）。
- 500：provider 错误（``ProviderError``）。

设计要点：
- 复跑式 ``run_agent``：每次都 ``create_adhoc_run`` 新建 run，避免 workflow_runs 行冲突；
  S4 正式工作流会改为复用同一 run_id。
- ``mock_script`` 透传：list 形式 → 按次返回；callable → 由 MockProvider 自行处理。
  None → 走 ModelRouter（生产路径）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.agent_runtime import (
    AgentOutputError,
    PromptNotFoundError,
    PromptRegistry,
    create_adhoc_run,
    run_agent,
)
from packages.core.logging_config import get_logger
from packages.core.model_router import ModelNotConfiguredError, ProviderError

log = get_logger("novelos.routers.agents")

router = APIRouter(tags=["agents"])


@router.post("/agents/sync", status_code=status.HTTP_200_OK)
def sync_prompts(request: Request, docs_dir: str = "docs/agents/prompts") -> dict:
    """从 docs_dir 扫描 prompt md 文件并 upsert。"""
    settings = request.app.state.settings
    result = PromptRegistry(settings.db_path).sync_from_docs(docs_dir)
    log.info(
        "prompt sync: scanned=%d registered=%d updated=%d",
        len(result["scanned"]),
        len(result["registered"]),
        len(result["updated"]),
    )
    return result


@router.get("/agents")
def list_agents(request: Request) -> list[dict]:
    settings = request.app.state.settings
    return PromptRegistry(settings.db_path).list_agents()


@router.get("/agents/{name}/prompts")
def list_agent_prompts(name: str, request: Request) -> list[dict]:
    settings = request.app.state.settings
    rows = PromptRegistry(settings.db_path).list_prompts(name)
    if not rows:
        # 区分「agent 不存在」与「agent 存在但无 prompt」；MVP 简化为空
        return rows
    return rows


@router.post("/agents/{name}/run", status_code=status.HTTP_201_CREATED)
def run_agent_endpoint(name: str, payload: dict, request: Request) -> dict:
    """手工触发一次 agent 调用。请求体：

    ```json
    { "input_payload": {...}, "expected": "observer"|"director"|"writer"|null, "mock_script": [...]|null }
    ```
    """
    settings = request.app.state.settings
    input_payload = payload.get("input_payload")
    if not isinstance(input_payload, dict):
        raise HTTPException(status_code=422, detail="input_payload must be an object")

    expected = payload.get("expected")
    if expected is not None and expected not in ("observer", "director", "writer"):
        valid = "observer/director/writer"
        raise HTTPException(
            status_code=422,
            detail=f"expected must be one of {valid} or null; got {expected!r}",
        )

    mock_script = payload.get("mock_script", None)
    # mock_script 必须为 list[str] / callable / None
    if mock_script is not None and not isinstance(mock_script, (list, str)) and not callable(mock_script):
        raise HTTPException(status_code=422, detail="mock_script must be list[str], str, callable, or null")

    run_id = create_adhoc_run(settings.db_path)
    try:
        output = run_agent(
            settings.db_path,
            name,
            input_payload,
            run_id,
            expected=expected,
            mock_script=mock_script,
        )
    except PromptNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelNotConfiguredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_error", "message": str(exc), "status_code": exc.status_code},
        ) from exc
    except AgentOutputError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "agent_output_invalid", "message": str(exc)},
        ) from exc
    return {"run_id": run_id, "agent": name, "output": output}


__all__ = ["router"]
