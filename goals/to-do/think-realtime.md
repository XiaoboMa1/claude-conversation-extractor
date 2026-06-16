# 功能扩展 ：实时渲染 thinking block

## 背景
Claude Code 把每个**会话（session）**写成一个 jsonl 文件，路径是 `~/.claude/projects/{编码后的项目路径}/{session-uuid}.jsonl`（Windows 下 `%USERPROFILE%\.claude\projects\...`）。一个项目目录下可能有多个 session 文件。

模型产生的 thinking 以 `{"type":"thinking","thinking":"..."}` 形式作为一个 block 落进 jsonl 的对应行里，即使终端不显示它，文件里也有。注意两点限制：

- jsonl 里是**摘要版** thinking，不是逐字原始推理链。这个工具能拿到的，和补丁 / Ctrl+O 在终端显示的是同一份内容。
- **（待验证，影响"实时"的真实含义）** Claude Code 写 jsonl 是"边生成边追加"还是"整条 assistant 消息生成完才写一行"。如果是后者（更可能），那么本工具能做到的是"**每一轮回复完成的瞬间打印该轮 thinking**"，而**不是**生成过程中逐字流式。这决定了你能不能靠它实时叫停——大概率不能。动手前先验证（见末尾）。

## 问题陈述

做一个终端工具：输入一个 id 前缀（至少前6位），定位到`%USERPROFILE%\.claude\projects\{编码后的项目路径}\{session-uuid}.jsonl`，持续监视该文件新追加的内容，把其中的 thinking 文本解析出来打印到终端。其余内容（user/assistant 正文、tool 调用）默认不打印。

## 输入 / 输出

### 输入：命令行参数，一个前缀字符串，例如
```
extract --think 7c3e9f
```
- 不需要输入 `编码后的项目路径`，程序递归搜索 seesion-id
- Session ID，至少传入前六位，更多不限

### 行为

1. 前缀解析。在 `~/.claude/projects/` 下递归找文件名以该前缀开头的 `*.jsonl`。
   - **（待决策）** 前缀匹配的是 session 文件名（UUID）还是项目目录名。session 文件名是 UUID，"前 6 位"对它才自然；项目目录是路径编码的长串，不是 6 位 id。建议匹配 session 文件名。
   - **（待决策）** 一个前缀命中多个文件时怎么办：列出让你选，还是自动取最近修改的那个。建议默认取 `mtime` 最新的。
2. 监视（tail）。`seek` 到文件末尾，轮询读取新追加的行。
3. 解析。每读到一个完整行（以 `\n` 结尾），`json.loads`，遍历找 thinking 文本。
4. 打印。

### 输出示例

当 Claude Code 写入这样一行：
```json
{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"用户要前缀匹配，先列出 projects 下所有 jsonl..."},{"type":"text","text":"我来帮你实现..."}]}}
```
工具打印：
```
── thinking <session-name-in-jsonl> 14:32:07 ──
用户要前缀匹配，先列出 projects 下所有 jsonl...
```
同一行里的 `text` block 不打印。

## 技术约束与边界情况

1. **tail 实现**：用轮询，不用第三方库。记录已读到的文件偏移量，每隔固定间隔（如 0.3s）读新增字节。
2. **半行缓冲**：Claude Code 可能写到一半还没写完 `\n`，读到的尾部是残缺 JSON。必须把不完整的片段缓存，等下次补齐到换行符再 `json.loads`，否则会抛解析异常。
3. **文件尚不存在**：启动时目标 session 可能还没创建。匹配不到就轮询等待，文件出现后再开始 tail，不要直接报错退出。
4. **JSON 结构容错**：thinking 字段的具体嵌套路径不同版本可能不同。不要写死 `obj["message"]["content"][0]`；遍历 `content` 数组找 `type=="thinking"` 的 block，取其 `thinking` 字段，找不到就跳过这一行。
5. **编码**：固定 UTF-8 读取，thinking 内容含中文等非 ASCII。
6. **依赖**：Python 标准库即可（`pathlib`/`os`、`json`、`time`、`sys`）。不引入 watchdog 之类，降低装环境的麻烦。
7. **跨平台路径**：同时支持Windows和Linux，用 `Path.home()/".claude"/"projects"`，不要硬编码 `~` 或正斜杠。

## 当前环境
- Claude code terminal - version NPM 2.1.83
- Why not native installer / upgrade: Native installer and latest npm versions hide Thinking process to the user, I hate this lack of transparency

## uncertainity and challenge : 在当前 session /clear 或 /rewind 后自动匹配并跟随到新文件

每次启动 Claude Code 算一个 session，对应一个 jsonl 文件，文件名是 UUID。你的工具启动时锁定了文件 7c3e9f....jsonl 并在 tail 它。
- 问题：有社区报告称 /clear 或 /compact 或 /rewind 启动 → 新 session，写进新文件 a1b2c3....jsonl，老文件 7c3e9f... 从此不再有新行，此时你的工具还盯着老文件，永远等不到新 thinking
- 要求：工具要检测到同一项目目录里冒出了与该seesion衍生的更新 jsonl，自动切过去 tail 新文件，无需手动重启工具、用新前缀再跑一次

## 写代码前的前置验证

打开一个真实 jsonl 确认字段和 /clear /compact 后 session 如何匹配，如 C:\Users\lenovo\.claude\projects\D-----UK-job-enea\cb15eb3d-856b-4006-90ac-685a5d3d240a.jsonl

（当前以下代码可工作）
import json
path = r"C:\Users\lenovo\.claude\projects\D-----UK-job-enea\cb15eb3d-856b-4006-90ac-685a5d3d240a.jsonl"
with open(path, encoding="utf-8") as f:
    for line in f:
        obj = json.loads(line)
        # 先 print(obj) 看一两行，确认 thinking 在哪一层
        # 常见是 message.content 数组里某个 block 的 type == "thinking"，文本在它的 thinking 字段
        for block in obj.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "thinking":
                print(block.get("thinking", ""))）
