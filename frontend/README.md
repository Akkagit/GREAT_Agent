# GREAT Agent frontend

本地 PPP-FLT 实验规划与运行监控界面。浏览器只连接本机 `/api/v1`，模型密钥由后端读取，不进入前端配置或构建产物。

```powershell
npm install
npm test -- --run
npm run typecheck
npm run build
```

开发时可通过 `VITE_API_BASE_URL` 修改 API 基础路径；应用从查询参数 `?experiment=<id>` 读取实验 ID。
