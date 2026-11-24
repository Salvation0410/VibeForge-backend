# 单审查输出异常诊断实施计划

**目标：** 落实已授权交接P0，保持原严格失败行为，补齐安全原因与异常阶段摘要。

**架构：** 模型解析器产生固定原因，验证节点最佳努力保存已有历史投影；不改变父图、公共协议或模型预算。

## 任务一：解析原因及日志

文件：`ai-service/src/ai_service/models/single_quality_review.py`、`models/openai_compatible.py`、`tests/test_quality_review_diagnostics.py`及新专项测试。

- [ ] 先写分类测试并确认旧实现失败。例如：

```python
with pytest.raises(SingleReviewOutputError) as caught:
    parse_single_review('{"verdict":"REPAIR","issues":[]}', 'STOP')
assert str(caught.value) == 'QUALITY_REVIEW_INVALID_OUTPUT'
assert caught.value.output_reason == 'VERDICT_ISSUES_MISMATCH'
```

- [ ] 新增固定白名单与output_reason；结束原因仍优先、重复字段不导出键名、schema异常只看固定类型，成功接受集合保持不变。模型日志追加outputReason，不带原文或异常正文；日志失败保持原结果，供应商异常和取消仍传播。
- [ ] 执行 `uv run pytest -o addopts='' tests/test_quality_review_diagnostics.py tests/test_review_output_reasons.py -q`，包含无重试与安全日志断言。

## 任务二：验证历史及异常留痕

文件：`models/verification_diagnostics.py`、`orchestration/nodes/verification.py`及新`tests/test_review_output_failure_history.py`。

- [ ] 先写真实嵌套图回归：Fake model.review抛出原因明确的SingleReviewOutputError，检查最后摘要有quality_review/ERROR及outputReason，而公共failed仍只有原错误码；旧实现应缺失该记录。
- [ ] 扩展历史白名单：旧字段原样通过；新记录仅允许两种解析错误码、quality_review/ERROR、0问题、固定outputReason，INCOMPLETE原因与错误码严格配对。未知或额外字段整体拒绝。
- [ ] 节点捕获仅限解析错误与非法注入返回值，调用已有 `_with_diagnostics` 生成记录，经 `_checkpoint_payload` 保存后裸raise原异常。不返回成功、不调用repair或publish。普通保存错误用固定日志处理，取消必须传播。
- [ ] 验证保存前后取消、保存故障、后续摘要保留、同轮替换、两次投影、旧记录兼容、诊断不入父图/模型/事件；运行相关诊断及子图测试。

## 任务三：复审、验证、交付

- [ ] 对照设计复审生产差异，进行独立只读审查。
- [ ] 执行Python compileall、lock检查及相关测试；完整回归应先排空并停止本地Python，避免重排器锁竞争，完成后恢复服务。保留失败和跳过项真实结果。
- [ ] 显式PostgreSQL唯一行事务验证后回滚；真实请求仅走既有预算代理同一账本，剩余20次内最多发起一次业务请求，不重跑追求PASS；结束恢复正常配置并停止代理。
- [ ] 更新README、AGENTS当前边界及交接第八节，记录真实错误原因是否观察到、验证与未测项、下一轮工作。
- [ ] `git diff --check`与秘密/提交范围检查后，按中文Conventional Commit提交代码与必要测试、文档，交付哈希。
