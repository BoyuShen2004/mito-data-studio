# Frontend 导读

React/TypeScript 负责页面、显微图像工作台和待保存编辑；后端仍然负责权限和持久化。

从 [main.tsx](src/main.tsx) → [AppRoutes.tsx](src/routes/AppRoutes.tsx) → 对应 `pages/` → `components/` 或 `features/` → `api/` 阅读。
相邻的 `*.test.tsx` / `*.test.ts` 给出行为例子；`e2e/` 保存浏览器回归场景。

常用命令（在本目录）：`npm run dev`、`npm run typecheck`、`npm test`。
生产产物使用 `npm run build:production`；普通 `npm run build` 包含开发账号构建标志。

继续阅读[代码地图](../docs/engineering/code-map.md)、[功能追踪](../docs/engineering/feature-walkthrough.md)和[第一次贡献](../docs/getting-started/first-contribution.md)。
