"""连接层（model_connections）突变规格（2026-10-21 批次十五）。

六条，每条对应一个「撤掉修复 ⇒ 必红」的判别点：

- ``L10-MC-01`` 保留键集合去掉 ``headers`` ⇒ headers 进上游请求体 ⇒ **400**。
- ``L10-MC-02`` 绑定态不覆写 provider ⇒ 档案的 provider 列能盖过连接 ⇒
  「连接是 provider 唯一权威」这条不变量破。
- ``L10-MC-03`` 读路径不掩 headers 里的敏感头 ⇒ **明文鉴权头回吐给前端**。
- ``L10-MC-04`` PATCH 的 ``""`` 不当作删除 ⇒ 头删不掉（且与「``***`` 保留」混为一谈）。
- ``L10-MC-05`` health_check 不在模型列表端点 404 时退化 ⇒ 「测试连接」假失败。
- ``L10-MC-06`` **撤掉**连接级 secrets 通道（``secrets[connection_id]``）⇒
  绑定态下改连接上的 key 不生效。

  ⚠️ 首版写的是「把 connection_id 与 profile_id 两级**交换顺序**」——**该突变存活**，
  且存活是**正确的**：当前调用约定下绑定态传 ``profile_id=None``、未绑定态不传
  ``connection_id`` ⇒ 两个分支**永不同时生效** ⇒ 顺序差异**不可观测**
  （坑区纪律「脚本报存活时先怀疑量具、再怀疑变异」：这次是**变异写法测不到机制**，
  不是被测物有洞）⇒ 改成「撤掉整条连接级通道」，那才是可观测的语义。

⚠️ 锚点均为**逐字**取自源文件；锚点命中数必须恰为 1（``validate_specs.py`` 预检）。
"""

MUTATIONS = [
    (
        "L10-MC-01",
        "packages/core/model_router/effective.py",
        '''        "timeout_s",      # 本地超时
        "headers",        # 自定义请求头，不进请求体
        "models_url",     # 模型列表端点（仅探测用）''',
        '''        "timeout_s",      # 本地超时
        "models_url",     # 模型列表端点（仅探测用）''',
        "tests/unit/test_model_connections.py",
    ),
    (
        "L10-MC-02",
        "packages/core/model_router/effective.py",
        '''        eff_provider = (connection_row or {}).get("provider") or profile_row.get("provider")''',
        '''        eff_provider = profile_row.get("provider")''',
        "tests/unit/test_model_connections.py",
    ),
    (
        "L10-MC-03",
        "packages/core/model_router/security.py",
        '''    out = dict(headers)
    for k in list(out.keys()):
        if isinstance(k, str) and k.strip().lower() in SENSITIVE_HEADER_NAMES:
            out[k] = _MASK
    return out''',
        '''    return dict(headers)''',
        "tests/api/test_model_connections_api.py",
    ),
    (
        "L10-MC-04",
        "packages/core/model_router/security.py",
        '''        if v is None or (isinstance(v, str) and v == ""):
            out.pop(k, None)''',
        '''        if v is None:
            out.pop(k, None)''',
        "tests/unit/test_security_headers.py",
    ),
    (
        "L10-MC-05",
        "packages/core/model_router/providers.py",
        '''        if resp.status_code in (404, 405, 501):
            # 端点不存在 ⇒ 退化探测（见 _probe_chat 的 docstring）
            return self._probe_chat(timeout=timeout, start=start, models_status=resp.status_code)''',
        '''        if False:  # MUTATION-L10-MC-05: 撤掉退化探测
            return self._probe_chat(timeout=timeout, start=start, models_status=resp.status_code)''',
        "tests/unit/test_model_connections.py",
    ),
    (
        "L10-MC-06",
        "packages/core/secrets_store.py",
        '''    if connection_id and secrets.get(connection_id):
        return secrets[connection_id]
''',
        '''    # MUTATION-L10-MC-06: 撤掉连接级 secrets 通道
''',
        "tests/unit/test_model_connections.py",
    ),
]
