# Morphmind Specialist Card UI Brief

这份文档是给 UI 实现模型或设计同伴的方向 brief。它不是严格像素稿，也不是组件逐行改造清单。

目标是让实现者在不破坏产品逻辑的前提下，有足够空间发挥审美、配色、动效和卡牌形态。

## 1. 核心产品方向

Blueprint RE 的默认界面应该像一个智能生信项目工作台，而不是 workflow IDE、后台管理系统或 raw graph editor。

用户主要做两件事：

- 和 Manager AI 聊天，提出需求、询问结果、审核 proposal。
- 查看一组小型 specialist cards，理解当前蓝图有哪些分析模块、状态如何、产出了什么文件或结果。

主界面可以收敛为：

```text
Manager AI Chat  <->  Specialist Card Deck
```

不需要默认三栏布局。不需要固定右侧 selected detail panel。不需要独立 ResultCard 作为主卡片。

## 2. 必须保留的硬约束

这些不能为了视觉效果牺牲：

- Manager AI 必须走真实后端接口，不允许 mock-only 或 fallback 成固定文案。
- 蓝图新增、修改、删除、恢复必须通过 Manager AI proposal 和后端校验。
- Proposal 的 Accept / Modify / Reject 操作必须清楚、可用、可追踪。
- 后端错误必须显示出来，不能吞掉。
- Results / files 应该挂在对应 module 或 run card 里，不作为主流程里的独立 ResultCard。
- 默认 UI 不展示 raw graph、patch JSON、manifest、hash、storage URI。
- 高级技术信息可以折叠到 advanced / technical details。
- 移动端也要能完成聊天、看卡、翻卡、发送结果给 Manager。

## 3. 可以自由发挥的部分

这些地方可以大胆设计：

- 整体视觉气质。
- 背景材质。
- 卡牌形状。
- 卡牌翻页方式。
- Specialist avatar 风格。
- 状态色系统。
- 卡牌分组方式。
- 文件袋形态。
- 微交互和动效节奏。
- 字体组合。
- 空间层次和装饰元素。

只要不破坏真实数据链路和 proposal 审核流程，视觉方案可以有明显个性。

## 4. 期望的整体感觉

关键词：

- Morphmind
- living blueprint
- specialist colony
- intelligent lab desk
- soft sci-fi
- organic cards
- compact but expressive
- playful but credible

界面可以有一点可爱感，但不要变成儿童游戏。它仍然是生信分析项目工具。

## 5. 布局建议

桌面端建议两区：

```text
┌────────────────────────┬──────────────────────────────┐
│ Manager AI             │ Specialist Card Deck          │
│ chat / tools / proposal │ modules / runs / result files │
└────────────────────────┴──────────────────────────────┘
```

比例可以自由调整，建议 Manager AI 占 36%-44%，Card Deck 占 56%-64%。

Card Deck 不一定要传统列表，可以尝试：

- 小卡网格。
- 浮岛 cluster。
- 状态分组 colony。
- 类似桌面上散落的卡牌。
- 轻微 mind-map 感的布局。

移动端建议：

```text
Chat | Blueprint
```

Card 详情和 file bag 可以用 bottom sheet。

## 6. Specialist Card

每张卡代表一个分析 specialist / subagent / module。

默认卡片尽量小。一屏最好能看到多个模块。

默认页只需要：

- 像素或插画头像。
- 模块名称。
- 状态。
- 一句话进度。
- 可选 result/file 数量。

示例：

```text
[头像] PCA Specialist
planned
等待执行
```

或者：

```text
[头像] DEG Agent
accepted · stable
已完成差异表达分析
```

## 7. Card Pages

卡牌可以做成多页，不要求真的 3D 翻转。

推荐至少三页：

- Specialist page：头像、名称、状态、当前进度。
- Result page：关键结果、图表缩略图、表格摘要、Manager review、Send to Manager。
- Detail page：目的、输入、输出、最近 run、参数摘要、风险提示、操作入口。

翻页方式可以自由：

- 翻牌。
- 横向 slide。
- 卡片内层切换。
- 卡角 page curl。
- 卡牌展开 morph。

注意：不要让动效影响阅读效率。

## 8. File Bag

文件不要铺满主界面。建议通过 card 内的 file bag 查看。

File Bag 可以设计得有记忆点，例如：

- 从卡牌侧边弹出的小文件袋。
- 像实验记录夹一样展开。
- 像小抽屉一样滑出。
- 像卡牌背后的附件层。

内容可分：

- Results：图、表、文本摘要。
- Logs：运行日志、warning、error。
- Technical：manifest、asset id、hash，默认折叠。
- Actions：Send to Manager、Download、Add to Report。

每个结果或文件都应该能 `Send to Manager`。

## 9. Manager AI 联动

Manager AI 是解释、判断和修改蓝图的主入口。

卡牌上的操作不要直接修改蓝图，而是把上下文发送给 Manager。

常见入口：

- Ask Manager
- Send Result
- Send File
- Explain this run
- Request rerun
- Modify module
- Delete module
- Restore module

发送到聊天框时，建议显示 attachment pill：

```text
[Attached: PCA Specialist] [Attached: variance_plot.png]
请解释这个结果，判断是否需要补充批次效应检查。
```

前端不要伪造完整结果总结。应该发送引用，由后端 Manager tool 读取真实内容。

## 10. Proposal UI

Proposal 仍然放在 Manager Chat 中。

建议做成和普通聊天不同的 decision card：

- 标题。
- 变更摘要。
- 影响到哪些 specialist cards。
- 风险或假设。
- Accept / Modify / Reject。
- 当前 proposal 状态。

Card Deck 可以配合做视觉反馈：

- 新 proposal 出现 ghost card。
- accept 后 ghost card 变实。
- delete 后 card 进入 dormant / cancelled 状态。
- restore 后 card 重新点亮。

## 11. 审美方向可以任选其一或混合

下面是一些可以尝试的方向，不是强制要求。

### Soft Sci-Fi Lab

- 深色背景。
- 柔和 cyan / green / amber 状态光。
- 半透明玻璃卡片。
- 微弱粒子或扫描线。
- 看起来像智能实验舱。

### Cozy Research Desk

- 暖色纸张、便签、文件袋。
- 卡牌像实验记录卡。
- 背景有轻微纸纹或桌面材质。
- 更亲和，不那么赛博。

### Pixel Agent Colony

- 每个 specialist 是小像素角色。
- 状态通过头像动作表达。
- 卡牌像小徽章或小精灵档案。
- 可爱但保持克制。

### Organic Mind Map

- 卡片像漂浮细胞。
- 模块 cluster 像神经连接。
- 背景有流动渐变。
- 更接近 Morphmind 的有机感。

实现者可以选择一个主方向，不必同时塞满所有风格。

## 12. 配色建议

配色不用写死，可以从这些语义出发：

- 背景：深墨、蓝黑、暖灰、实验室暗色台面都可以。
- 主表面：玻璃、纸张、半透明卡片、柔和实体卡都可以。
- accepted：稳定、可信、完成感。
- planned：安静、待开始。
- proposed：尚未实体化、半透明、幽灵感。
- running：正在工作、有节奏。
- cancelled：休眠、可恢复，不是彻底消失。
- error：明确但不要吓人。

避免：

- 所有状态只靠文字区分。
- 大面积刺眼红色。
- 默认紫白 SaaS 风。
- 纯表格后台风。

## 13. 动效建议

动效可以有个性，但必须有意义。

适合做：

- 卡牌进入时轻微 stagger。
- hover 时头像有一点生命感。
- running 状态有低频呼吸。
- 翻页有轻微 morph。
- file bag 从卡牌里展开。
- proposal accept 后 ghost card 变实。
- 发送附件到 Manager 时出现 attachment token。

不适合做：

- 长时间、重 3D、阻塞操作的动画。
- 每个元素都在动。
- 影响文字阅读的漂浮。

需要支持 `prefers-reduced-motion`。

## 14. 组件改造参考

当前可能涉及：

- `frontend/components/layout/ProjectWorkspace.tsx`
- `frontend/components/manager-chat/ManagerChatPanel.tsx`
- `frontend/components/cards/CardStream.tsx`
- `frontend/components/cards/ModuleCard.tsx`
- `frontend/components/cards/RunCard.tsx`
- `frontend/components/cards/ResultCard.tsx`
- `frontend/components/detail/CardDetailPanel.tsx`
- `frontend/components/results/ResultPreviewPanel.tsx`
- `frontend/app/globals.css`

建议方向：

- `ProjectWorkspace` 改为双区布局。
- `ManagerChatPanel` 保留真实接口，增强 proposal 和 attachment UI。
- `CardStream` 可演化为 specialist deck。
- `ModuleCard` 成为核心卡牌。
- `RunCard` 可以嵌入 module card 或作为卡牌内 run summary。
- `ResultCard` 不再作为主 stream 的独立卡片。
- `CardDetailPanel` 不再是默认三栏，必要信息移入 card page / file bag。
- `ResultPreviewPanel` 可以复用在 result page 或 file bag。

## 15. 最小验收标准

完成后至少满足：

- 主界面是 Manager AI + Specialist Cards 的双核心结构。
- 卡片比原来更小，一屏能看到多个 specialist。
- 卡片能查看 specialist / result / detail 三类信息。
- 文件和结果能从卡片内打开，而不是作为独立主卡片。
- 用户能把某个 card、result 或 file 发送给 Manager AI。
- Proposal 接受、修改、拒绝链路仍然可用。
- 后端错误不会被隐藏。
- 默认不暴露 raw JSON、hash、manifest、patch。
- 移动端可以完成核心操作。

## 16. 最重要的一句话

请优先做出一个有生命感、能让用户愿意和 Manager AI 以及 specialist cards 互动的界面；业务链路必须严谨，但审美和表现形式可以大胆发挥。
