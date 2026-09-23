# spark 服务事实、使用礼仪与隐私红线

## 服务现状（2026-09，由管理员 hezq1 维护）

| 项 | 值 |
|---|---|
| 硬件 | NVIDIA DGX Spark（GB10 统一内存架构，aarch64 20 核，119 GB 统一内存） |
| 推理服务 | Docker 容器 `clinical-vllm`（vLLM，host 网络，端口 8000） |
| 模型 | **Qwen3.6-35B-A3B-FP8** |
| served 名 | `clinical-qwen36-35b`（推荐）；别名 `clinical-qwen3-4b`（历史名，同一模型） |
| API key | `local-vllm` |
| 关键参数 | max-model-len **32768**；服务端并发上限 **8**（max-num-seqs）；prefix caching 开启 |
| KV cache | ~268 万 token（32K 上下文下约 82 路理论并发，实际以 8 为准） |
| 请求必带 | `temperature: 0`；`chat_template_kwargs: {"enable_thinking": false}` |
| 同机其他服务 | docling-serve、open-webui（勿动） |

OpenAI 兼容端点（经 lab-connect 隧道后）：`http://127.0.0.1:18000/v1`
（`/models` 列模型，`/chat/completions` 对话）。

## 共享服务器礼仪

spark 是**共享** GPU 服务器，多课题同时使用：

1. **跑大批量前先确认没人在用**：发个 1-token ping 看响应是否正常，或问群里/管理员；
   有人在大跑任务时错峰再跑。
2. **并发封顶 8**：`--workers 8`（管线默认值已是 8）。KV 余量大不等于可以打满——
   服务端 max-num-seqs 就是 8，再高只是本地排队。
3. **只通过 API 使用模型**：不要登录服务器重启/删除/改动 `clinical-vllm` 容器与 compose
   配置；不要 kill 别人的进程；不要在服务器上留大文件。
4. **服务异常的正确姿势**：`check_connection.py` 失败、模型列表为空、响应持续超时 →
   找管理员（hezq1），不要自行处置。
5. 长任务用断点续跑（管线自带 checkpoint），中断随时可续，不追求一口气跑完。

## 患者数据隐私红线

1. **数据不出内网**：患者文本只发给 spark 上的本地模型。不发给任何云端 API、
   不贴进任何外部工具/网站，除非用户明确声明已获合规授权。
2. **prompt 里不放可识别信息**：patient_sn 等标识只用于结果对齐，不进 prompt 文本；
   姓名、住院号等直接标识符更不允许。
3. **产物管理**：含患者数据的输出（runs/、merged/ CSV）只存本地/授权存储，
   **永不提交进 Git**、不上传网盘。
4. **机器判定不是结论**：模型标记（双靶候选、NED 等）只是候选，发表/汇报前必须人工裁决。
5. 遵守所在实验室与医院的数据管理规定；有疑问先问数据负责人，再动手。
