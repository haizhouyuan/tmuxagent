---
name: storyapp-ci-hardening
description: 完成 CI 质量闸门补强（前端检测、Playwright 扩展、脚本修复）。
model: gpt-5-codex
---
目标：
1. 在 `.github/workflows/ci.yml` 中加入前端 lint、type-check、必要单元测试；确保 Playwright 测试并行或按项目拆分以控制时长。
2. 修正 `package.json` / workspace 脚本，使 `npm test` 与 `npm run lint` 在仓库根路径可执行；消除 `storyapp_test` pane 中的 ENOENT 问题。
3. 更新 Playwright 配置或测试集，使根脚本覆盖所有关键路径，并产出清晰报告。
4. 编写/更新文档，说明新的 CI 要求与本地校验步骤。
流程：
- 拉取最新 master，创建 feature 分支。
- 修改并验证：本地跑 lint/test/build，Playwright 并行测试。
- 更新 docs/ci 指南。
- 推送 PR，总结风险与后续建议。
遇到不确定的工具版本、执行超时等问题，请暂停并向 orchestrator 汇报。
