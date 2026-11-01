# 群聊自然语言画图

群友向机器人提出画图请求时，现有 Decisions 分诊会结合最近对话判断是否需要生成；无需固定命令。只有正常回复门放行、判断明确选择生成时才调用图像接口。判断不可用或拿不准时不生图；查看、描述或修改已有图片也不会被当成新图生成。

默认使用 OpenAI `gpt-image-2.5-flare`，`medium`、`1024x1024`，每群每人每天基础 5 次；关系达到熟人、好朋友、铁哥们、本命时，分别为 6、7、9、13 次。每天全局最多发起 40 次，因此多人同时使用时可能先碰到全局上限。每次请求前持久化计数，失败或超时也计入，不自动重试；同一时刻只处理一个生图请求。超过几秒时先回复等待，再发送图片。图片只在内存中交给 QQ 上传，不写入文件。QQ 最终发送结果无法确认时，不假定已经失败，也不重复发送。

第一次使用好感度带来的额外份额时，机器人可以说这次破例；额外份额本身仍有上限。额度用完会告诉群友今天累了，明天再画。搜索同样按群友和关系档位单独限额，默认基础 20 次，四个关系档依次为 25、30、40、50 次，同时受每群搜索上限约束。额度在本地持久化，次日重置。

在 `.env` 中可配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMAGE_GENERATION_ENABLED` | `true` | 关闭设为 `false` |
| `IMAGE_GENERATION_BASE_URL` | `OPENAI_BASE_URL`，未设置时为 `https://api.openai.com/v1` | 支持 Images API 的地址 |
| `IMAGE_GENERATION_API_KEY` | 空 | 优先使用；否则使用 `OPENAI_API_KEY` 或 `api_keys_openai.txt`；双方地址均为官方 OpenAI 时还可复用面板的判断 API 密钥 |
| `IMAGE_GENERATION_MODEL` | `gpt-image-2.5-flare` | 图像模型 |
| `IMAGE_GENERATION_QUALITY` | `medium` | 输出质量 |
| `IMAGE_GENERATION_SIZE` | `1024x1024` | 输出尺寸 |
| `IMAGE_GENERATION_DAILY_CAP` | `40` | 全局每天最多发起的请求次数，设为 `0` 停用付费调用 |
| `IMAGE_USER_DAILY_CAP` / `SEARCH_USER_DAILY_CAP` | `5` / `20` | 每群每人的基础日额 |
| `IMAGE_USER_EXTRA_*` / `SEARCH_USER_EXTRA_*` | 见 `.env.example` | 按关系档位增加的额外次数 |
| `IMAGE_GENERATION_TIMEOUT_SECONDS` | `150` | 单次生成超时，单位秒 |

模型账号必须有相应的 Images API 权限和额度；判断密钥可用并不保证图像模型也可用。该功能目前是群聊文生图，不接收参考图片编辑，也不保留生成图片供后续修改。判断 API 不可用时不会退回关键词触发。
