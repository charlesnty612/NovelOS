"""`SnapshotResponse` schema 声明面收口的突变规格（2026-10-16 批次八 · 主会话自办）。

为什么补这一组
--------------
批次（三）给 `GET /state` 与 `GET /state/versions/{n}` 补了
`response_model=SnapshotResponse`，把「前端欠定义类型把 characters 拍成 {}、
两个统计项恒 0」那个缺陷修掉了。但**当时的守卫只断言了 schema 存在、且被端点引用**，
没有断言它的 `properties`。于是：

    从 SnapshotResponse 删掉任一字段声明 ⇒ 端点响应**一字不变**（extra="allow"）

⇒ 批次（三）那个缺陷**静默复活**，且当时 5 条用例**全绿**。

这与批次（五）M20 是同一条纪律的第二处实例：`extra="allow"` 让「删字段声明」
**没有可见症状**，因此必须断言 **schema 的 `properties` / `required`**，
光断言响应键集抓不到。

三条突变覆盖三个独立失效方向：
* FIELD-1 删 `hooks` 声明 ⇒ 少一个 property（属性面）；
* FIELD-2 删 `events` 声明 ⇒ 同上但取的是「本批被显式钉过形状的那个段」；
* REQ-1 把 `state_version` 改成有默认值 ⇒ 从 required 掉出去（必填面）。
  只钉属性面会漏掉这一类——它响应照样对，只是 schema 不再要求它。
"""

MUTATIONS = [
    (
        "L5-FIELD-1",
        "packages/core/story_state/models.py",
        """    hooks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="伏笔列表（按 hook_id ASC）",
    )
""",
        "",
        "tests/api/test_snapshot_response_contract.py",
    ),
    (
        # ⚠️ 首轮这条**存活**，真因是**变异写错了**（不是守卫测不到）：初版 replacement 写成
        # `events: dict[str, Any] = {}` —— 那**仍然声明着 events 这个字段**（只是没了
        # description 与 default_factory）⇒ 属性面一字未改 ⇒ 用例当然全绿。
        # 「改变了字节但没有改变行为」——脚本报存活时先怀疑变异本身，见坑区
        # 「突变本身也要突变」。runner 的 `写入后 SHA256 未变` 那条断言**抓不到它**
        # （字节确实变了），只能靠**看这条变异是否真删掉了那个字段**。
        "L5-FIELD-2",
        "packages/core/story_state/models.py",
        """    events: dict[str, Any] = Field(
        default_factory=dict,
        description="剧情事件，**按 event_id 键的字典**（不是列表）",
    )
""",
        "",
        "tests/api/test_snapshot_response_contract.py",
    ),
    (
        "L5-REQ-1",
        "packages/core/story_state/models.py",
        """    state_version: int = Field(
        description="快照版本号；无快照时后端返回 0（genesis 未初始化前的形态）",
    )""",
        """    state_version: int = 0  # MUT: 有默认值 ⇒ 掉出 required""",
        "tests/api/test_snapshot_response_contract.py",
    ),
]
