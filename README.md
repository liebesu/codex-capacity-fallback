# codex-capacity-fallback

一个面向 Codex CLI 的小型、可审计模型回退工具，用来缓解：

> `Selected model is at capacity. Please try a different model`

它在明确识别到服务端容量错误时，按用户配置的模型顺序执行有限次回退，并使用有上限的指数退避和抖动。认证失败、额度耗尽、策略拒绝、未知错误和已经出现工具调用迹象的回合不会自动切换。

## 能解决什么

- 为 `codex exec` 增加一次性的模型候选链，例如 Astra → Sol → Luna。
- 识别 HTTP 状态之外的 SSE/文本错误，因为容量错误可能嵌在流事件里。
- 尊重输出中的 `Retry-After`，否则使用有限的指数退避。
- 记录只包含模型、错误类别、耗时和退出码的 JSONL 审计信息，不记录提示词、输出或密钥。
- 默认阻止带有 `tool_call`、`function_call`、`command_execution` 等标记的回合重放，避免重复副作用。

## 不能解决什么

这个项目不能增加 OpenAI/ChatGPT 服务端容量，也不能解锁套餐、配额或 ChatGPT 网页端的模型选择。ChatGPT 网页和桌面端不读取本项目的命令行配置；如果网页端报错，只能等待服务恢复、手动选择当前可用模型，或向官方支持提供 request ID。

把请求切到第三方或自建 provider 也不是容量解锁：那会改变计费、数据处理、模型能力和工具兼容性。请只使用自己批准、已验证且符合组织政策的 provider。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
```

也可以直接从源码运行测试：

```bash
python -m pip install ".[test]"
python -m pytest
```

## 使用

最小示例：

```bash
codex-capacity-fallback \
  --models gpt-6-astra,gpt-6.1-sol,gpt-6-luna \
  -- \
  codex exec --json "总结当前仓库的测试状态"
```

如果第一个模型返回容量错误，工具会等待后把同一个命令切换到下一个模型。模型必须是当前账户、工作区和 Codex 客户端实际可用的值；默认列表只是示例，推荐显式配置：

```bash
export CODEX_FALLBACK_MODELS="gpt-6.1-sol,gpt-6-luna"
codex-capacity-fallback -- codex exec --json "整理 TODO"
```

查看计划而不执行：

```bash
codex-capacity-fallback \
  --models gpt-6.1-sol,gpt-6-luna \
  --dry-run -- \
  codex exec --json "hello"
```

写入脱敏审计记录：

```bash
codex-capacity-fallback \
  --log-file .codex-capacity-fallback.jsonl \
  -- codex exec --json "只读分析"
```

只有在你确认整轮请求是幂等的、并且允许重复执行时，才使用：

```bash
codex-capacity-fallback \
  --allow-partial-replay \
  --models gpt-6.1-sol,gpt-6-luna \
  -- codex exec --json "只执行幂等的只读检查"
```

## 策略说明

默认只对明确的容量签名触发切换：`Selected model is at capacity`、`server_is_overloaded`、`server overloaded` 和 `PolicyCapacityUnavailable`。错误分类优先级是容量、部分执行、认证、策略、限流、网络和未知错误。

`--max-attempts` 不会超过模型列表长度；`--max-delay` 会限制等待时间；`--jitter` 防止多个调用同时重试。整个项目只控制一层重试，避免 SDK、网关和脚本叠加放大请求量。

## 官方依据

- [OpenAI 官方：Designing failover across OpenAI model providers](https://learn.chatgpt.com/blog/designing-failover-across-openai-model-providers) 说明了容量错误的候选模型切换、`Retry-After`、有界指数退避、共享重试预算和副作用保护。
- [ChatGPT Learn：Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference) 记录了 Codex 的 `model`、自定义 `model_providers`、`request_max_retries` 和 `stream_max_retries` 配置项。
- [ChatGPT Learn：Models](https://learn.chatgpt.com/docs/models?surface=cli) 说明 ChatGPT Work/Codex 的模型选择受计划、工作区、客户端和 rollout 影响。

## 开发

```bash
python -m pip install ".[test]"
python -m pytest
```

本项目使用 MIT License。欢迎提交 issue，附上脱敏后的错误类别、客户端版本和 request ID；不要提交 API key、OAuth token、提示词或完整对话内容。
