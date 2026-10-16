"""章节归属「两种失败返回同一形状 404」的突变规格（2026-10-16 批次十 · Lead 自办）。

三个独立失效方向：

* **OWN-1** 权威 helper 对「不属于本 project」退回 400 + 泄露 detail（修前的行为）
  ⇒ 守「政策落在唯一的判定处」；
* **OWN-2** `continuation.py` 对跨项目 404 但 detail 仍写 `does not belong`
  ⇒ 守「第二处泄露点」（它不调 helper，是独立的一份实现）；
* **OWN-3** `story_state.py::_ensure_chapter_in_project` 退回「捕获 400 再改写」
  的 workaround 形状 ⇒ 守「workaround 已被删，别悄悄加回来」——
  它的危害不是响应错，而是**给 helper 将来新增第三种退出码留了一个会被静默改写的口子**。

⚠️ 本规格的判别文件里那条**源码级形状守卫**（扫 router 层有没有把归属线索写进
detail）是形状级判别：它把「判别面」从「已知三条路径」扩到「全仓新增端点自动进域」，
所以 OWN-2 的泄漏即使发生在**没被点名的**端点上也会红。
"""

MUTATIONS = [
    (
        "OWN-1",
        "packages/core/api/routers/workflows/common.py",
        '''    if chapter_pid != project_id:
        log.info(
            "chapter 不属于 project，按 404 处理：chapter=%s project=%s", chapter_id, project_id
        )
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")''',
        '''    if chapter_pid != project_id:
        raise HTTPException(
            status_code=400,
            detail=f"chapter {chapter_id!r} does not belong to project {project_id!r}",
        )''',
        "tests/api/test_chapter_ownership_status.py",
    ),
    (
        "OWN-2",
        "packages/core/api/routers/continuation.py",
        '''        log.info(
            "chapter 不属于 project，按 404 处理：chapter=%s project=%s", chapter_id, project_id
        )
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")''',
        '''        raise HTTPException(
            status_code=404,
            detail=(
                f"chapter {chapter_id!r} does not belong to project {project_id!r}"
            ),
        )''',
        "tests/api/test_chapter_ownership_status.py",
    ),
    (
        # 只加回 try/except 骨架、不改响应 ⇒ 撤掉它不应让**响应类**断言变红，
        # 但源码形状那条会因「detail 里出现 belong」而红。
        "OWN-3",
        "packages/core/api/routers/story_state.py",
        '''    from packages.core.api.routers.workflows.common import check_chapter_in_project

    check_chapter_in_project(request.app.state.settings.db_path, project_id, chapter_id)''',
        '''    from packages.core.api.routers.workflows.common import check_chapter_in_project

    try:
        check_chapter_in_project(request.app.state.settings.db_path, project_id, chapter_id)
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        raise HTTPException(
            status_code=404,
            detail=f"chapter {chapter_id!r} does not belong to project {project_id!r}",
        ) from exc''',
        "tests/api/test_chapter_ownership_status.py",
    ),
]
