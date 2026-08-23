# desktop（前端工作台）

> 职责：NovelOS 的本地 Web 前端——React 19 + TypeScript + Vite 8 单页应用，Sprint 0 仅实现「后端健康状态展示」最小页，作为后续 Sprint 5 Workbench UI 的入口骨架。
> 状态：已实现 Sprint 0 骨架（页面可访问 `http://127.0.0.1:5173`，通过 vite proxy 调 `/api/health`）。

## 职责与边界

**做**：
- 启动 Vite 开发服务器（`127.0.0.1:5173`），代理 `/api/*` 到 FastAPI（`127.0.0.1:8000`）
- 渲染根组件 `App`，进入时拉取 `/api/health` 并展示后端状态
- 生产构建：前端产物由后端静态托管（参见 `docs/impl/IMPLEMENTATION-PLAN-v0.md` D-I1 Phase A）

**不做**：
- 不实现项目管理 / Story Bible / 章节编辑 / AI Panel（属于 Sprint 5 Workbench UI）
- 不实现任何 LLM 直连调用（前端只走 `/api/*` 后端代理）

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `App` | `apps/desktop/src/App.tsx:10` | 根组件，`useState<Health>` + `useEffect` 拉 `/api/health` |
| `Health` 类型 | `apps/desktop/src/App.tsx:4` | `{status, version, tables}` |
| `main.tsx` 入口 | `apps/desktop/src/main.tsx:1` | `createRoot(...).render(<StrictMode><App/></StrictMode>)` |
| `vite.config.ts` proxy | `apps/desktop/vite.config.ts:11` | `/api → http://127.0.0.1:8000` |
| `package.json scripts.dev` | `apps/desktop/package.json:6` | `vite` |
| `package.json scripts.build` | `apps/desktop/package.json:7` | `tsc -b && vite build` |
| `package.json scripts.lint` | `apps/desktop/package.json:9` | `oxlint` |

## 依赖

**运行时**：react@^19.2.8、react-dom@^19.2.8。

**开发**：@vitejs/plugin-react@^6.0.4、vite@^8.2.0、typescript@~6.0.2、oxlint@^1.75.0、@types/*。

**后端代理**：FastAPI 服务（`packages/core/api`）。

## 使用 / 入口

```bash
# 1. 启动后端（另一终端）
python scripts/serve.py

# 2. 启动前端 dev server
cd apps/desktop
npm install   # 首次
npm run dev   # → http://127.0.0.1:5173

# 生产构建
npm run build   # 产物在 apps/desktop/dist，由后端托管
```

页面行为：进入后展示「Backend health」卡片，1 秒内显示 `{status, version, tables}`；后端不可达时显示 `unreachable: HTTP <code>`。

## 维护注意点

- **端口固定**：`vite.config.ts` 用 `strictPort: true`，5173 被占用即启动失败（避免与后端 8000 混淆）。
- **CORS 对齐**：vite dev proxy 已处理跨域；若改用后端静态托管，需调整 `apps/desktop/vite.config.ts` `build.outDir` 与后端静态文件挂载。
- **不引入 UI 库**：Sprint 0 仅用原生 CSS（`App.css` / `index.css`），符合 PRD §111 「管理台风格，不做复杂可视化」。
- **package name**：`"desktop"`（保留 Vite 模板命名）；后续若新增移动端或 CLI 形态再改名。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I1（本地 Web 形态）、§2 Sprint 5（Workbench UI 任务）。