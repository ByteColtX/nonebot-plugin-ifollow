---
id: models
type: module-design
status: draft
title: 订阅数据模型与持久化契约
---

## Responsibility

`models.py` 持有订阅数据的内存模型，并作为插件唯一的写盘入口：作者 → 目标 →（各 feed 的游标、
订阅人）。网络、消息发送与新旧作品判断都不在这里。

## Boundary

- 输入 / 输出：`AuthorSubscription`、`TargetSubscription`、`Cursor`。
- 持久化：插件数据目录下的 `subscriptions.json`，由 `nonebot-plugin-localstore` 定位。
- 不判断作品新旧、不发送消息、不感知平台接口。

## 结构

一个作者可被多个会话订阅，每个会话各自记录游标，所以同一作者的推送互不影响；
`target_key` 由目标身份字段（`id` / `parent_id` / `channel` / `private` / `adapter` / `scope` /
`platforms`）规范化成排序后的 JSON 生成，任一字段变化即视为另一个目标。

## 版本与迁移

存储版本 2，可读入 v1：v1 的游标是纯字符串，读入成「无发布时间」的游标，写回统一为 v2 结构。

## 决策

- **只在内容变化时写盘**：序列化结果与上次写入相同时直接跳过，避免每轮轮询都产生磁盘写。
- **原子写 + 线程**：同目录临时文件 + `os.replace` 原子替换，写文件放进线程执行，
  事件循环不被磁盘 I/O 阻塞，也不会在磁盘上留下半写的文件。
- **快照是深拷贝**：`snapshot()` 返回副本，调用方改游标必须走 `update_cursor`，不能就地改内部状态。
- **读失败整体回退**：版本或结构不认识时告警并使用空订阅，不半读半丢。
- **级联删除**：目标没有订阅人、或删除最后一个目标时，连带删掉作者条目。
- **游标不因失败回退**：只由轮询在发送成功后推进，store 不判断对错。
