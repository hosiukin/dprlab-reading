---
name: dprlab-reading
description: Connect to the lab spark clinical-LLM server and run medical-record reading (病历读取/病史提取) workflows. Use whenever the user wants to: connect to spark / set up lab-connect or EasyConnect jump-host SSH / call clinical-qwen36-35b or clinical-qwen3-4b / do 病历读取、病史结构化提取、文本清洗 / unify patient IDs (patient_sn、住院号 inpatient、门诊号 outpatient) / organize multi-document patient texts (多次住院病史、病理报告、多次影像检查) / run or adapt the reading-pipeline example. Also covers port-forward tunnels (本地 18000 → spark 8000) and shared-GPU etiquette.
---

# dprlab-reading — spark 临床模型连接与病历读取

帮用户完成两件事（按顺序）：

1. **Phase 1 连接**：用 [lab-connect](https://github.com/hosiukin/lab-connect) 配置
   `本机 → EasyConnect → 跳板 → spark` 的 SSH 链路和端口转发，调通临床模型服务。
2. **Phase 2 病历读取**：以**用户自己的想法为主**，用本项目（BRAF n316，316 例）验证过的
   清洗与读取策略，处理**用户自己的**表格数据——清洗、患者 ID 处理、多文本整理、
   病史/病理/影像读取、QC 与人工裁决。范例管线仅供参考，不强制套用。

**工作方式**：先读速查表和对应 reference，再动手；每阶段结束向用户汇报并确认下一步。

## 关键参数速查表

| 项 | 值 |
|---|---|
| 模型服务 | spark 服务器 vLLM 容器 `clinical-vllm`（Qwen3.6-35B-A3B-FP8） |
| 模型名 | `clinical-qwen36-35b`（别名 `clinical-qwen3-4b`，同一模型） |
| API key | `local-vllm` |
| 服务端口（spark 侧） | 8000（host 网络） |
| 本地转发端口 | **18000** → spark `127.0.0.1:8000`（经 lab-connect） |
| 跳板 | `10.30.24.1:22`（用户各自的账号） |
| spark | `10.33.250.29:22`（用户各自的账号，经 ProxyJump） |
| 必须的请求参数 | `temperature: 0`；`chat_template_kwargs: {"enable_thinking": false}` |
| 推理上下文 | max-model-len 32768；服务端并发上限 8（管线 `--workers 8` 即够） |
| 管线环境变量 | `RP_BASE_URL=http://127.0.0.1:18000/v1` `RP_MODEL=clinical-qwen36-35b` `RP_API_KEY=local-vllm` |
| 本地验证脚本 | `scripts/check_connection.py`（本 skill 目录下） |
| 范例管线 | `examples/reading_pipeline_2026-08-31/`（BRAF n316 项目内为 `scripts/reading_pipeline_2026-08-31/`） |

## Phase 0 前置检查（动手前先确认）

- [ ] 操作系统与 Python ≥ 3.10（`python --version`）；`pip show pandas`（管线必需）
- [ ] EasyConnect 已连接（没连则先让用户启动并登录，否则跳板不可达）
- [ ] 用户有跳板账号和 spark 账号（各自的名义账号，不是共享账号）
- [ ] 用户已有 SSH 密钥对（没有则 `ssh-keygen -t ed25519`，或用 lab-connect 向导部署）
- [ ] 磁盘上有输入数据的位置，或已拿到数据（患者数据只能在内网/授权环境流转）

## Phase 1 连接（详细步骤 → `references/connection.md`）

1. `git clone https://github.com/hosiukin/lab-connect.git`
2. 启动向导：Windows 双击 `start-windows.bat`（SmartScreen → More info → Run anyway）；
   macOS 双击 `start-macos.command`
3. 在向导中创建 profile（名字建议 `spark`，它就是 ssh 别名）：
   - jump host `10.30.24.1`、jump 用户 = 用户自己的账号
   - target host `10.33.250.29`、target 用户 = 用户自己的账号
4. Port forwards 页添加：名称 `spark-vllm`，本地端口 `18000`，目标侧主机 `127.0.0.1`，远端端口 `8000`，模式 forwarding only
5. Save and start all forwards
6. 验证：`python <skill目录>/scripts/check_connection.py`
   （默认探测 `http://127.0.0.1:18000/v1`；通过 = 列出模型 + 1-token 对话正常）

排障（详见 connection.md）：连接被拒 = 转发未启动；exit 255 / 超时 = EasyConnect 未连或账号错误；
401 = key 不对；404 = base_url 少了 `/v1`；本机有代理时脚本已强制直连。

## Phase 2 病历读取（用户想法优先）

**先摸底再动手**（2a）：问清用户有什么表格（病史/病理/影像检查）、ID 字段叫什么、想提取什么字段。
不要预设用范例管线；数据形态和目标字段由用户定义。

然后按三篇 reference 推进：

- **2b/2c 数据准备** → `references/data_prep.md`：表格清洗（GBK 编码、错位表头、重复行）、
  **patient_sn 唯一标识符 + 保留 inpatient/outpatient ID 建映射表**、
  同一患者多次文本资料整理（病史多文档选择/归并、病理报告、多次影像检查组织与治疗窗对齐）。
- **2d 读取策略** → `references/methodology.md`：温度 0 + 关思考 + 严格 JSON、
  分块与跨块去重、锚点→窗口两步法、证据锚定（evidence_verbatim 回文校验）、
  领域词表与幻觉剔除、执行 vs 建议区分、QC 指标、**机器判定必须人工裁决**。
- **2e 范例参考** → `references/pipeline_example.md`：BRAF n316 可运行管线（四步、断点续跑、
  merge+QC）。数据形态接近（patient_sn + conclusion 两列）可直接用；否则按 2b–2d 策略自建。

服务与礼仪（模型参数、共享服务器规则、隐私红线）→ `references/server.md`。

## 红线（每次会话都要遵守）

1. **患者数据不出内网**：只发文本给 spark 本地模型；不把患者数据发给任何云 API 或外部服务
   （除非用户明示确认合规）。
2. **共享服务器礼仪**：跑大批量前先确认服务器没被别人占用；`--workers 8` 封顶；
   **绝不重启/删除/改动 `clinical-vllm` 容器**；服务异常找管理员（hezq1），不要自行处置。
3. **机器判定只是候选**：所有模型产出的标记（如双靶候选）必须输出 evidence 供人工复核，
   不得直接当结论使用。
4. 运行产物（含患者数据的输出）放在本地 `runs/` 等目录，**永不提交进 Git**。
