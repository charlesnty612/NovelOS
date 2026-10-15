"""plot 脏行降级修复的突变规格（2026-10-15 批次（六）· 主会话自办）。

缺陷：``_row_to_event`` / ``_row_to_relationship`` 走裸 ``json.loads``，一行脏数据
让**整个列表端点 500**（干净行陪葬）。修法：service 侧 ``_safe_json`` 兜底。

三条分别对应三个失效方向：形状不对的合法 JSON / 语法就坏的文本 / 另一张表。
"""

MUTATIONS = [
    (
        "L0-PLOT1",
        "packages/domain/plot/service.py",
        'cause=_safe_json(row["cause_json"], []),',
        'cause=json.loads(row["cause_json"]) if row["cause_json"] else [],',
        "tests/api/test_plot_dirty_json_row_degradation.py",
    ),
    (
        "L0-PLOT2",
        "packages/domain/plot/service.py",
        'state=_safe_json(row["state_json"], {}),',
        'state=json.loads(row["state_json"]) if row["state_json"] else {},',
        "tests/api/test_plot_dirty_json_row_degradation.py",
    ),
    (
        # 兜底退化成「只挡解析异常、不挡类型不符」⇒ 合法 JSON 但形状不对的行仍会 500
        "L0-PLOT3",
        "packages/domain/plot/service.py",
        """    if type(parsed) is not type(fallback):
        return fallback""",
        "",
        "tests/api/test_plot_dirty_json_row_degradation.py",
    ),
]
