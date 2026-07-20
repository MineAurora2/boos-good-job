# 隐藏重复在线实例数设计

## 目标

“脚本实时监控”的“已登记实例”指标只显示一个在线实例数值。保留 `connectedClients` 作为用户可见主数值，不再显示重复的 `activeClients` 数值及其心跳窗口说明。

## 现状与原因

`activeClients` 和 `heartbeatWindowHint` 已带有 HTML `hidden` 属性，但监控指标样式为 `strong` 和 `small` 设置了 `display: block`，覆盖浏览器对隐藏元素的默认处理，导致重复数值仍然可见。

## 设计

- 在 `.monitor-metrics` 范围内明确让带有 `[hidden]` 属性的元素保持 `display: none`。
- 保留隐藏节点和现有 JavaScript 更新逻辑，避免影响兼容调用或其他数据流。
- 不改变 `connectedClients`、运行实例数、今日投递数和筛选投递率的计算或展示。
- 不引入全局 `[hidden]` 规则，避免扩大到其他页面组件。

## 验证

- 打开 `/dashboard`，确认“已登记实例”卡片只出现一个数值。
- 确认 `activeClients` 与 `heartbeatWindowHint` 在 DOM 中不可见。
- 确认主数值仍随当前在线实例数更新。
- 检查页面无框架错误覆盖层，控制台无本次修改引入的错误或警告。

