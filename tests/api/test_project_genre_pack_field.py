"""题材库 P1b：Project 响应暴露 ``genre_pack_id``（列表与详情同步）。

覆盖：
1. 新建项目 → 详情 / 列表 ``genre_pack_id`` 均为 null（未绑定）；
2. 经 genre API 绑定 → 详情与列表同步显示 pack id；
3. 解绑 → 回 null。
"""

from __future__ import annotations

from pathlib import Path

from ._shared import _create_app, _request, _run

_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [{"type_id": "face_slap", "name": "打脸"}],
}








def test_project_response_exposes_genre_pack_id_binding_lifecycle(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        created = await _request(app, "POST", "/api/projects", json={"name": "题材项目"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert "genre_pack_id" in body
        assert body["genre_pack_id"] is None
        pid = body["project_id"]

        detail = await _request(app, "GET", f"/api/projects/{pid}")
        assert detail.status_code == 200
        assert detail.json()["genre_pack_id"] is None
        listed = await _request(app, "GET", "/api/projects")
        assert listed.status_code == 200
        assert listed.json()[0]["genre_pack_id"] is None

        pack = await _request(
            app, "POST", f"/api/projects/{pid}/genre-packs",
            json={
                "name": "男主快穿", "genre_tag": "快穿",
                "payload": _PAYLOAD, "pack_id": "gp_kc",
            },
        )
        assert pack.status_code == 201, pack.text
        bind = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": "gp_kc"})
        assert bind.status_code == 200, bind.text

        detail2 = await _request(app, "GET", f"/api/projects/{pid}")
        assert detail2.json()["genre_pack_id"] == "gp_kc"
        listed2 = await _request(app, "GET", "/api/projects")
        assert listed2.json()[0]["genre_pack_id"] == "gp_kc"

        unbind = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/unbind")
        assert unbind.status_code == 200, unbind.text
        detail3 = await _request(app, "GET", f"/api/projects/{pid}")
        assert detail3.json()["genre_pack_id"] is None

    _run(run())
