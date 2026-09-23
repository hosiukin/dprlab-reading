# dprlab-reading — spark 临床模型连接与病历读取 skill

让实验室成员在自己的电脑上，借助 AI 编程助手（ZCode / Claude Code 等支持 skill 的工具）
完成两件事：

1. **连接**：用 [lab-connect](https://github.com/hosiukin/lab-connect) 一键配置
   `你的电脑 → EasyConnect → 跳板 → spark` 的 SSH 链路与端口转发，
   调通实验室 spark 服务器上的临床大模型（Qwen3.6-35B-A3B-FP8，模型名 `clinical-qwen36-35b`）；
2. **病历读取**：在你的 AI 助手里说一句话（如"帮我连 spark 做病历提取"），它会引导你完成
   医院导出表格的**清洗、患者 ID 处理（patient_sn 唯一标识 + 住院/门诊号映射）、
   同一患者多次文本资料整理**，以及**病史、病理报告、多次影像检查**等文本的
   LLM 结构化读取、证据校验与 QC——策略来自 BRAF n316 项目（316 例）的完整实践，
   附带可运行的范例管线。

> **以你的想法为主**：skill 不会强制你套用某个管线；它提供经过验证的清洗与读取策略，
> 按你自己的数据形态和研究目标定制。

## 前置要求

| 条件 | 说明 |
|---|---|
| AI 编程助手 | ZCode 或 Claude Code 等支持 SKILL.md 格式的工具 |
| EasyConnect | 已安装并能登录（连接实验室网络的 VPN 客户端） |
| 跳板 / spark 账号 | 你自己的实验室账号（没有找管理员 hezq1 开通） |
| Python ≥ 3.10 | 跑管线用；另需 `pip install pandas`（`json_repair` 可选） |
| Git | 克隆本仓库与 lab-connect |

## 安装（把 skill 装进你的 AI 助手）

**第 1 步：克隆本仓库**（私有仓库，需先用你的 GitHub 账号获得访问权限）：

```bash
git clone https://github.com/hosiukin/dprlab-reading.git
```

**第 2 步：把 skill 目录复制到 AI 助手的 skills 目录**：

- **个人级（所有项目可用，推荐）**——Windows（PowerShell/CMD）：

  ```bat
  mkdir %USERPROFILE%\.zcode\skills 2>nul
  xcopy /E /I dprlab-reading\dprlab-reading %USERPROFILE%\.zcode\skills\dprlab-reading
  ```

  macOS / Linux：

  ```bash
  mkdir -p ~/.zcode/skills
  cp -r dprlab-reading/dprlab-reading ~/.zcode/skills/
  ```

- **项目级（只对某个项目生效）**：把 `dprlab-reading/dprlab-reading/` 复制到该项目的
  `.zcode/skills/`（或 `.agents/skills/`）目录下。

**第 3 步：验证**——重启/新开 AI 助手会话，对它说：

> 帮我连接 spark 做病历读取

它应该自动加载 dprlab-reading skill，从 Phase 0 前置检查开始引导你。

## 快速开始（装好后典型的一次使用）

1. 打开 EasyConnect 并登录；
2. 对 AI 助手说"帮我连接 spark"——它会引导你装好 lab-connect、配 profile（跳板
   `10.30.24.1`、spark `10.33.250.29`、你自己的账号）、开转发（本地 18000 → spark 8000），
   并运行 `check_connection.py` 验证；
3. 把你的数据表给 AI 助手（病史/病理/影像 CSV 或 Excel），说想提取什么；
4. 它会按 skill 里的策略清洗数据、建 ID 映射、设计提取任务，先小样本试跑
   （用 `examples/reading_pipeline_2026-08-31/assets` 里的合成样例 `sample_synthetic.csv`
   可以零风险练手），核对质量后放量，最后产出带证据校验的结构化表 + QC 报告。

## 目录结构

```text
dprlab-reading/                        # ← 复制到 skills 目录的就是这个文件夹
├── SKILL.md                           # 入口：速查表 + Phase 0/1/2 工作流
├── references/
│   ├── connection.md                  # lab-connect 安装配置全程 + 排障
│   ├── data_prep.md                   # 表格清洗 / patient_sn ID 体系 / 多文本整理策略
│   ├── methodology.md                 # LLM 读取与 QC 策略总纲（证据锚定、锚点→窗口、词表）
│   ├── pipeline_example.md            # 范例管线使用说明
│   └── server.md                      # spark 服务参数、共享服务器礼仪、隐私红线
├── scripts/
│   └── check_connection.py            # 连接验证（models + 1-token 对话）
└── assets/
    └── sample_synthetic.csv           # 3 例合成病历文本（纯虚构，练手用）

examples/
└── reading_pipeline_2026-08-31/       # 范例管线（BRAF n316 项目，可运行可改造）
    ├── pipeline.py                    # 四步：estimate / prepare / run / merge（断点续跑）
    ├── lexicon.py                     # 领域词表示例（换课题先换词表）
    ├── imaging_prepare.py             # 影像检查整理与治疗窗对齐示例
    ├── prompts/step{1..4}_*.md        # 提取提示词模板
    └── README.md                      # 详细用法
```

## 隐私与使用须知（务必阅读）

1. **患者数据不出内网**：文本只发给 spark 本地模型；不发给任何云 API / 外部服务。
2. **共享服务器礼仪**：spark 是多课题共用的 GPU 服务器——大批量任务前先确认没人占用；
   并发 ≤ 8；**不要动服务器上的 `clinical-vllm` 容器**；服务异常找管理员（hezq1）。
3. **机器判定只是候选**：模型输出的标记（如治疗方案类别）必须人工核对证据
   （`evidence_verbatim`）后才能用于结论。
4. 含患者数据的运行产物（`runs/` 等）**永不提交进 Git**、不上传网盘。
5. 本仓库为实验室内部私有仓库，请勿公开转发；内网地址与访问凭证不要外传。

## 谱系与致谢

策略与范例管线来自 BRAF n316 项目（316 例转移性结直肠癌病史整理与生存分析；
管线源 `work/reading_pipeline_2026-08-31/`，2026-08-31 在 Qwen3.6-35B 上完成全量提取：
2,204 次调用 0 失败、证据逐字命中率 73–88%）。连接工具为
[lab-connect](https://github.com/hosiukin/lab-connect)。维护：hezq1。
