# 第27轮混合检索实施计划

> 使用subagent-driven-development执行独立检索组件与复核；主流程负责输入绑定、真实执行与交付。用户已选定直接实施混合检索对照，不修改生产行为。

**Goal:** 固定语料验证dense与BM25/RRF混合Top8的检索证据增减。

**Architecture:** 纯排序模块与独立实验运行器分离；复用原正式选择/预算与本地BGE。固定新账本，旧向量只读。规格见同日rag-hybrid-round27-design.md，所有参数运行前冻结。

**Tech Stack:** Python、numpy、标准库、现有LocalCrossEncoderReranker；不增加依赖。

## 1. 排序组件及合成测试

- [ ] 新建orchestration/rag_hybrid_retrieval.py和tests/test_rag_hybrid_retrieval.py。
- [ ] tokenize(text)返回词元列表；bm25_top(chunks, question, limit=8)返回(index,score)；rrf_top(dense_indices, sparse_indices, limit=8)返回(index,score)，拒绝重复索引。全部按规格固定参数。
- [ ] 写手算公式、中文边界、空匹配、稳定同分、两路重叠、单路回退和Top8截断测试；先红后绿。排序不接受rubric。

## 2. 实验运行器和故障测试

- [ ] 新建orchestration/rag_hybrid_experiment.py及tests/test_rag_hybrid_experiment.py。
- [ ] 复用validate_bindings/load_candidates/reconstruct/dense_top8/select_contexts；独立有界读取vectors.json。验证旧批次fingerprint，所有90题旧Top8逐字段重现。
- [ ] prepare冻结模型文件、源码/锁/计划/语料/历史输入hash、每题两组输入hash与180项调度。plan写独立tmp；--live要求hash一致并新建固定锚点。
- [ ] 排他账本预占→BGE→正式选择→每项落盘；异常保留已完成结果和固定错误码，不重试。Fake测试核对预占先于调用、失败占额度、重复启动及输入漂移拒绝。
- [ ] 收尾计算事实引文阶段矩阵、两组增减、旧baseline差异、耗时、预算和人工模板。

## 3. 冻结与执行

- [ ] 定向测试通过并审查源码。执行prepare，核对新计划，代码/规格/计划先提交。
- [ ] 仅一次--live：180本地BGE上限，外部调用0。记录完整原文和结果于tmp，定期说明进度。停止于首错，禁止补跑。

## 4. 解释与交付

- [ ] 按最终变化事实逐条核对引文与范围风险，不把代理复核充当真人。
- [ ] 全量pytest、compileall、uv lock --check --offline；核对旧材料hash不变。
- [ ] 提交结构化结果、独立第27轮交接、两主交接增补、README任务附录；用户原改动不暂存。
- [ ] git diff --check及提交范围审核，中文提交；最终说明是否观察到提升、代价、未测项和下一步建议。
