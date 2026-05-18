# Git-native Bioinformatics Agentic Repo 文档包

建议阅读顺序：

1. `00_overview_blueprint.md`：总体设计和产品原则
2. `01_frontend_ui_blueprint.md`：前端 UI 信息架构、页面草图、组件拆分
3. `02_backend_implementation_blueprint.md`：后端模块、服务、API、运行闭环
4. `03_data_contracts_and_schemas.md`：核心 JSON 数据契约与示例
5. `04_vibe_coding_implementation_plan.md`：适合 AI 编程助手逐步实现的任务计划
6. `05_review_checklist.md`：容易出错节点的 Review 清单

核心产品原则：

- 用户只编辑意图，不直接编辑蓝图
- Manager AI 负责 proposal / patch
- 后端负责校验和应用 patch
- Worker Agent 尽量自由
- Graph Update 必须严格
- Git 保存每次 accepted 变化
- Card 默认隐藏 Graph IR 和版本管理复杂度
