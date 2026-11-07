# 群聊自然语言画图

画不画、画什么、要静图还是动图，由小团团在同一次回复里决定：第一行用「〔画〕」或「〔动图〕」加上给画师的完整描述，代码按标记选择输出格式。小团团那句话先发到群里，图跟在后面。Decisions 沿用[画图前检查](drawing-guard.md)，不另加一道生成类型分诊题。规则、标记格式和为什么这么分工，见[画图和语音](media-replies.md)。

## 群友只需说想看什么

例如「画一个正在打篮球的鸡的动图」。小团团保留这个主体和动作，自己补全成适合短循环的画师描述：鸡在球场上原地运球，球落地反弹、翅膀跟着摆动，回到起始姿势，镜头固定。群友不需要给分镜、帧数或接口参数；未要求动起来时，照常画静图。

同一条回复中的「〔动图〕」决定 `output_format="gif"`，描述决定画面怎样运动。单把「动图」写进画面描述不会改变接口输出格式。代码不靠枚举群友的说法选格式，也不额外调用模型改写提示词。

默认使用 OpenAI `gpt-image-2.6-flare`，质量和 `complexity` 均为 `medium`。静图为 `1024x1024`；动图为 `512x512`、9 帧、每帧 200 毫秒，即约 1.8 秒的循环 GIF。帧数、尺寸和时长由维护者配置，保持单次生成的开销有边界。增大帧数或尺寸时，要留意[官方 GIF 参数说明](https://developers.openai.com/api/docs/guides/image-generation#generate-animated-gif-output)中的总像素预算；共用请求次数不代表静图和动图的单次费用相同。

## 同一套额度与投递

静图、动图共用画图额度和单并发限制：每群每人每天基础 5 次；关系达到熟人、好朋友、铁哥们、本命时，分别为 6、7、9、13 次。每天全局最多发起 40 次，因此多人同时使用时可能先碰到全局上限。请求前持久化预留个人和全局次数；确定没有发出图片时返还个人次数，已发出的 QQ 消息如果回执不明，则保留个人次数，避免把可能已送达的图片当作失败。全局尝试上限保守计入已发起的请求：接口超时、审核或 QQ 上传失败时上游费用未必为零，不自动重试。同一时刻只处理一个生图请求。PNG 或 GIF 的 base64 原样交给 QQ 上传，不落盘、不转码。

按 [QQ 直传规范](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_files.post.html)附带文件名，并保守限制解码后的文件小于 5,000,000 字节。上传前验证 base64，动图还要有 GIF 文件头；服务返回静图或文件过大时按失败处理，不额外生成、不自动降级为静图。

第一次使用好感度带来的额外份额时，小团团会说这次破例；额外份额本身仍有上限。额度用完它会说今天累了，明天再画。搜索同样按群友和关系档位单独限额，默认基础 20 次，四个关系档依次为 25、30、40、50 次，同时受每群搜索上限约束。额度在本地持久化，次日重置。

同一时刻只画一张：小团团写回复之前就知道手上有没有一张没画完、这个人今天的额度还剩多少、这张是不是破例，用自己的话说出来。图没发出去时补一句本地话术，按原因分审核没过、服务出错、没发进群；回执不明不补。会话里记下这张画了什么、发没发出去。运行日志只记阶段、异常类型、HTTP 状态及白名单机器错误码，不记原始错误正文或提示词。

## 配置

在 `.env` 中可配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMAGE_GENERATION_ENABLED` | `true` | 关闭设为 `false` |
| `IMAGE_GENERATION_BASE_URL` | `OPENAI_BASE_URL`，未设置时为 `https://api.openai.com/v1` | 支持 Images API 的地址 |
| `IMAGE_GENERATION_API_KEY` | 空 | **通常不用填**：生图地址与聊天 `OPENAI_BASE_URL` 相同时，共用 `OPENAI_API_KEY` / `api_keys_openai.txt`。仅当生图指向另一家服务才在这里配专用 Key |
| `IMAGE_GENERATION_MODEL` | `gpt-image-2.6-flare` | 图像模型；动图需要支持 GIF 输出的模型和服务 |
| `IMAGE_GENERATION_QUALITY` | `medium` | 输出质量 |
| `IMAGE_GENERATION_COMPLEXITY` | `medium` | 渲染前规划量：`low` / `medium` / `high`；显式设空可不传，兼容旧模型或网关 |
| `IMAGE_GENERATION_SIZE` | `1024x1024` | 静图尺寸 |
| `IMAGE_GENERATION_GIF_ENABLED` | `true` | 开启短循环动图；服务不支持时关闭 |
| `IMAGE_GENERATION_GIF_SIZE` | `512x512` | 动图每帧尺寸，或 `auto` |
| `IMAGE_GENERATION_GIF_FRAME_COUNT` | `9` | 每张动图的帧数，2–64 |
| `IMAGE_GENERATION_GIF_FRAME_DURATION_MS` | `200` | 每帧 20–2000 毫秒，10 毫秒的整数倍 |
| `IMAGE_GENERATION_DAILY_CAP` | `40` | 全局每天最多发起的请求次数，设为 `0` 停用付费调用 |
| `IMAGE_USER_DAILY_CAP` / `SEARCH_USER_DAILY_CAP` | `5` / `20` | 每群每人的基础日额 |
| `IMAGE_USER_EXTRA_*` / `SEARCH_USER_EXTRA_*` | 见 `.env.example` | 按关系档位增加的额外次数 |
| `IMAGE_GENERATION_TIMEOUT_SECONDS` | `150` | 单次生成超时，单位秒 |

模型账号必须有相应的 Images API 权限和额度；判断密钥可用并不保证图像模型也可用。`complexity`、帧数和时长等新字段通过 SDK 的 `extra_body` 发送，兼容仓库现有 SDK。更换服务时，要核对它是否支持这些参数；关闭动图不会自动把一单动图伪装成静图成功。

该功能是群聊文生静图或短循环动图，不接收参考图片编辑，不保留生成图片供后续修改，也不生成视频。

2026-11-05 已完成一次真实验收：上述篮球小鸡短句经过回复模型补全动作、Decisions 检查与 Images API，生成了 512×512、9 帧、1.8 秒、830,171 字节的循环 GIF。随后复用同一文件完成 QQ 上传和发送，均收到成功回执，用户确认「群里看到动图了」。生图耗时约 14.6 秒；这是单个用例的观察，不是延迟保证。方法、调用次数和验证边界见[真实动图验收记录](gif-acceptance-2026-11-05.md)。
