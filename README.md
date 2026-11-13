# M-Team + qBittorrent

Hermes 原生插件，支持搜索馒头资源、查看详情，并将明确选择的种子提交到 qBittorrent。当前版本：**0.6.0**。

站点、下载器、凭据和默认保存路径均通过 Hermes Web Dashboard 配置。插件内附 Skill，无需单独安装。

## 功能

- 关键词搜索、实时分类筛选、分页和资源详情。
- 分页浏览配置的 qBittorrent 中全部任务，包括其他工具添加的任务，只读显示状态与进度。
- 经明确授权后按完整 hash 删除单个任务，默认保留文件；删除文件需另行确认不可恢复风险。
- 从站点读取分类名称、ID、父子关系及支持的分组，不使用硬编码分类 ID。
- 生成站点下载 URL 并直接提交给 qBittorrent，由下载器获取种子；不向模型返回带凭据的链接。
- 配置单一默认保存路径，下载时可指定其他绝对路径。
- 通过资源/请求标签关联任务并避免重复提交；已识别的任务不移动或重启，结果不确定时不自动重投。
- 支持添加后开始下载或保持暂停。
- 网页连接测试、版本检查及脱敏诊断。

仅用于有权获取和传播的资源，不绕过站点账户权限。

## 兼容性

- Hermes 集成基线：**v0.23.1**，Web Dashboard SDK **1.1.0**。其他版本需验证插件及 Dashboard 扩展接口兼容性。
- qBittorrent **4.x / 5.x**，需启用 WebUI。
- 种子解析、格式支持和获取限制由 qBittorrent 决定，Hermes 不再预先获取种子文件。
- 插件核心使用 Python 标准库，Web API 使用 Hermes 提供的 FastAPI，前端使用宿主 React，无需单独构建前端。

## 安装

仓库：<https://github.com/cyt-666/hermes-mteam-qbit>

通过 Hermes 插件安装入口安装此仓库，并启用 `mteam-qbit`。若安装入口要求仓库 URL，填写上面的地址。

也可手动将项目文件放入实际 Hermes home 下的 `plugins/mteam-qbit`：

```text
<Hermes home>/plugins/mteam-qbit/
├── plugin.yaml
├── __init__.py
├── core.py
├── settings.py
├── dashboard/
│   ├── manifest.json
│   ├── plugin_api.py
│   └── dist/index-0.6.0.js
└── skills/mteam-qbit/SKILL.md
```

不要多嵌套一层项目目录。Hermes home 通常为 `~/.hermes`，实际路径受运行用户、`HERMES_HOME` 和部署配置影响。容器部署应持久化 Hermes 数据目录，并保证 Dashboard 和 Agent 能读取插件文件。无需挂载下载目录或 Docker socket 到 Hermes。

可通过插件管理页面启用，或执行：

```sh
hermes plugins enable mteam-qbit
```

首次安装或更新代码后，重新加载使用插件的 Agent/Gateway 和 **提供网页的 Dashboard 进程**，再刷新浏览器。只重启 Gateway 不会更新独立 Dashboard 中已加载的后端代码。

若整个 Hermes 由一个 Compose 服务运行，可按部署方式重启该服务（将 `hermes` 替换为实际服务名）：

```sh
docker compose restart hermes
```

Dashboard 可独立管理时，只需重启相应进程，不要求重启整个容器。Hermes v0.23.1 的已挂载插件 API 不会因替换文件或刷新页面自动热更新。页面显示前后端版本，不一致时暂停保存及连接测试。

## 网页配置

打开 Hermes Web Dashboard 侧栏的 **M-Team 下载**。工具集选择弹窗只控制工具启用，不是配置页面。

| 配置项 | 说明 |
|---|---|
| 馒头 API 地址 | 默认 `https://api.m-team.cc`，以站点可信说明为准，仅允许 HTTPS |
| 馒头 API Key | 在网页填写；已有值不回显，留空保留，输入新值替换 |
| 馒头 User-Agent | 默认 `MTeamQBit/1.0`；可配置 1–256 位可打印 ASCII，不允许换行 |
| qBittorrent WebUI 地址 | 如同一 Docker 网络内的 `http://qbittorrent:8080`，或服务可达的主机地址与端口 |
| qBittorrent 用户名/密码 | 网页保存，不回显；留空保留已有值 |
| 默认保存路径 | qBittorrent 所在环境内的绝对路径，如 `/downloads` |
| qBittorrent 类别 | 默认 `hermes`，用于下载器任务分类，与馒头搜索分类不同 |
| 添加后暂停 | 默认关闭；打开后提交的任务保持暂停/停止状态 |

容器中的 `localhost` 指容器自身。若 qBittorrent 运行在另一个容器或主机，应使用其可达地址。跨公网使用 HTTPS，不要公开裸露 WebUI。

点击 **保存配置** 后再测试：

- **测试馒头**：调用 `/api/member/profile`，验证业务成功及响应结构，丢弃账户资料。不验证搜索或下载能力。
- **测试 qBittorrent**：登录并读取版本，不添加任务，也不验证保存路径写权限。

连接测试使用已保存配置，每个 profile、每项服务限每 15 秒一次。更换地址后，已有凭据将用于新地址，保存前应确认目标可信。

### 高级设置

网页支持配置下载域名白名单、搜索模式、搜索/详情/下载令牌端点、详情 form/JSON 编码、origin，以及准备请求有效期（默认 900 秒，范围 60–86400 秒）。

下载域名须为精确可信主机名，不含协议、路径或通配符。提交的种子 URL 仅允许 HTTPS/443。Hermes API 请求不跟随重定向；提交后的取种、跳转及网络策略由 qBittorrent 控制。Hermes 发往 M-Team 的 API 请求使用配置的 User-Agent，不保存或发送 Cookie；API Key 不转发至种子 URL。qBittorrent 使用自己的取种请求头和网络/代理配置，不继承插件 User-Agent。qBittorrent 单独保留登录 Cookie。

## 使用方法

启用插件时自动注册捆绑 Skill，名称为 `mteam-qbit:mteam-qbit`。Hermes 可通过技能列表发现并读取它；也可明确要求“使用 mteam-qbit:mteam-qbit 技能”。Skill 注册不代表每次请求都会自动读取全文。

### 搜索与分类

可直接对 Hermes 说：

- “列出馒头当前可用的分类。”
- “搜索 ××，每页 10 条，只列结果，不下载。”
- “用刚才列出的分类名称筛选，查看下一页。”
- “查看资源 123 的详情。”

搜索支持以下 8 种模式：`normal`、`adult`、`movie`、`music`、`tvshow`、`anime`、`rankings`、`all`。网页可选择默认模式，初始为 `normal`；对话中可指定本次 `mode`，不会改写保存的默认值。翻页保留返回的模式，切换模式从第一页开始，不会因结果为空自动更换范围。

`mode` 控制搜索视图/范围，`categories` 在该范围内进一步筛选；`category=all` 只表示不限制分类，并不等于 `mode=all`。例如可明确说“使用 movie 模式搜索 ××，再按刚才查到的分类筛选”。特殊模式的可见性及返回内容由站点与账号权限决定。实站模式探测只能确认请求是否成功及结果数量，不能单凭成功码证明服务端完整执行了每种模式的筛选语义。

分类名称须匹配实时返回的简体、繁体或英文名称，也支持分组键及数字 ID。父类名称展开为实时子孙叶类；同名分类需指定 ID。数字 ID 原样提交，不保证服务器接受。分类目录受账户可见性影响，也不保证所有分类都可在当前 `search_mode` 搜索，插件不会自动切换分区。

分页从 1 开始，最多 1000 页，每页 1–50 条。下一页保持关键词、筛选和页大小，改变关键词或筛选时从第一页开始。非空 `category_ids` 可原样回传；返回 `category_ids=[]` 表示全部分类，下一页应省略该参数并使用 `category=all`。返回结果包含安全提取的 `category_id`。分页优先使用服务端实际 `pageSize` 计算后续页，保留请求的 `requested_limit`，下一页继续传 `requested_limit`，不要把服务端实际 `limit` 回填为请求参数；站点未提供可靠总量时，不根据短页猜测是否结束。

### 提交下载

例如：

- “下载资源 123，使用默认保存路径。”
- “下载资源 123 到 /downloads/movies。”
- “查看刚才下载任务的状态。”

下载分为准备和提交两步。准备时返回资源摘要和固定保存路径；明确选择资源并要求下载后，才提交到 qBittorrent。仅搜索不是下载授权。用户已经明确指定资源并要求下载时，不需要机械重复确认，但仍遵守 Hermes 的审批与权限设置。

**保存路径属于 qBittorrent 所在环境。**容器部署填写 qBittorrent 容器内路径，不是宿主机路径或 Hermes 容器路径。省略路径使用网页默认值；可指定其他绝对 POSIX 路径，不实施根目录白名单。路径只做格式校验，不验证存在性、可写性或符号链接目标。

准备完成后修改网页默认值不会改变该请求的路径；需要改变路径应重新准备。发现已有插件资源标签任务时保留原任务路径和状态，不移动、不重启。插件不预先获取种子，无法提前计算 info-hash；未带插件标签的既有任务只能依赖 qBittorrent 自身去重，不保证插件可以提前识别。

### 工具一览

| 工具 | 用途 |
|---|---|
| `pt_categories` | 读取实时分类，需要已保存 API Key |
| `pt_search` | 搜索；支持 query、page、limit、mode、category、category_ids |
| `pt_details` | 读取 resource_id 详情 |
| `pt_destinations` | 查看默认保存路径 |
| `pt_prepare_download` | resource_id 与可选 save_path，生成请求摘要和 request_id |
| `pt_add_download` | request_id 与 user_confirmed，提交已获授权的请求 |
| `pt_download_status` | 查询准备/提交请求状态 |
| `pt_delete_download` | 精确 hash 删除单个任务，默认保留文件；删除文件需单独明确确认 |
| `pt_list_downloads` | 分页浏览 qBittorrent 全部任务，page 默认 1，limit 默认 20、最大 100 |

`present` 表示任务已可见，不表示文件下载完成；`submitted_not_yet_visible` 表示已应答但尚未查到任务；`unknown` 表示结果不确定，应先在 qBittorrent 核对，不能自动重投。`resource_already_submitted` 返回原 request_id，应查询原请求；`failed_before_submission` 表示尚未尝试向下载器提交，可重新准备。

URL 提交是异步操作：下载器接收请求不代表已获取种子或开始下载，应通过任务标签继续查询。签名链接在提交时生成，不持久化到插件数据库；链接失效或下载器网络失败时，不会自动刷新链接重投。已提交或结果不确定的同资源请求会阻止再次提交，需先人工核对下载器。此保护也会阻止删除下载器任务后自动重加同一资源；当前没有清除该提交记录的工具，不要直接删除数据库绕过保护。

### 查看下载器全部任务

可说“查看 qBittorrent 所有任务，每页 20 条”“下一页”。此列表不局限于本插件添加的任务，按添加时间倒序分页，提供任务状态、进度等必要信息，不返回 tracker、磁力链接或完整原始响应。列表变化时分页可能重复或遗漏，应刷新后再核对；没有可靠总数时不虚构总数。

查看任务不会更改下载器队列。插件不提供暂停、恢复或移动已有任务的工具。

### 删除任务

先列出任务并核对名称、完整 hash、保存路径，再明确要求删除某一个任务。默认只删除任务，保留文件。需要同时删除文件时，必须在执行前明确确认具体任务、文件及可能不可恢复的后果；不能把“删除任务”解释为“删除文件”。

`pt_delete_download` 接收 `torrent_hash`、`user_confirmed`，以及默认 false 的 `delete_files` 和 `delete_files_confirmed`。两个确认字段必须反映真实用户确认，不得由模型自行补全。一次仅允许一个完整 40/64 位 hash，不支持 all 或批量。删除请求由 qBittorrent 执行，插件不在宿主机删除文件。

删除后的状态核查只验证任务是否消失，不证明文件是否已彻底清除。网络失败或未确认消失时不要盲目重试，应先查询下载器。删除任务不会清除插件已有提交记录或自动解除防重投保护。

## 安全与数据存储

- 普通配置保存在当前 profile 的 `mteam-qbit/config.json`，原子替换并使用 0600 权限；状态保存在同目录 SQLite。
- API Key、用户名和密码通过 Hermes 原生凭据接口保存，不写入普通配置 JSON，也不回传模型。文件权限和不回显不等于静态加密。
- API 使用 Hermes 宿主认证和 profile 范围，不单独开放端口。外部环境或托管凭据来源可能影响最终生效值，应避免重复配置冲突。
- `user_confirmed` / `delete_files_confirmed` 是模型声明，不是独立、不可绕过的审批系统。插件没有额外的分渠道/分用户授权；只向可信用户开放提交工具。
- 下载目录安全边界由 qBittorrent 的挂载范围和运行用户权限决定。避免特权容器及不必要的可写挂载。
- 签名 URL 本身具有访问权限，会发送到配置的 qBittorrent；下载器自身日志可能记录完整 URL，应限制其日志访问，不要公开分享。
- 种子提交后可能下载和做种。资源确认绑定 ID、标题和路径，需信任站点在准备与提交间不替换内容。
- 诊断仅提供阶段、状态和受限错误信息；上游 message 会做长度限制和保守脱敏，但不能保证识别任意个人信息。分享截图或日志前仍需检查，不公开密钥、Cookie、Authorization 或完整签名链接。

## 更新与停用

通过原安装方式更新插件文件，保留 profile 中的配置和 SQLite 状态。更新后重新加载 Agent/Gateway 与 Dashboard，再刷新页面，核对前后端版本一致。不要用空挂载覆盖已有 Hermes 数据目录。

停用可在插件管理页操作，或执行：

```sh
hermes plugins disable mteam-qbit
```

按部署方式重新加载相关进程。停用不会删除 qBittorrent 任务、已下载文件或插件状态。

## 开发与验证

```sh
python -m unittest discover -s tests -v
node --check dashboard/dist/index-0.6.0.js
# 可选前端 DOM 模拟测试
cd tests/ui && npm install --ignore-scripts && npm test
```

API 测试使用 fastapi/httpx；隔离 HTTPS 集成测试需要 openssl。前端测试依赖位于 `tests/ui/package.json`。

当前版本通过 84 项 Python 测试及 JavaScript 语法检查。测试覆盖本机隔离 HTTPS 模拟服务、Cookie 隔离、URL 提交、异步标签查询、分页列表、两种删除模式和错误诊断；删除测试仅针对模拟服务。模拟服务不是实际 M-Team 或 qBittorrent，不能替代真实环境端到端验证。此前版本通过 DOM 模拟测试，当前版本未重新执行该项。

`.gitignore` 排除配置、env、SQLite、种子和缓存，不能清理已提交到历史的秘密。项目当前未指定开源许可证。

## 参考

- [Hermes v0.23.1](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1)
- [Hermes Web Dashboard SDK](https://github.com/NousResearch/hermes-agent/blob/v0.23.1/web/src/plugins/sdk.d.ts)
- [Hermes Skill 注册](https://github.com/NousResearch/hermes-agent/blob/v0.23.1/hermes_cli/plugins_content.py)
- [M-Team 分类接口公开适配](https://github.com/libertypi/mtsearch/blob/main/mtsearch.py)
- [M-Team SDK 分类结构](https://github.com/llbox/mt-sdk/blob/52e7beb91954/pkg/mt/enums.go)
- [MoviePilot M-Team 适配](https://github.com/jxxghp/MoviePilot/blob/v3/app/modules/indexer/spider/mtorrent.py)
- [qBittorrent WebUI API](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29)

站点接口参考公开客户端实现，不构成上游长期兼容性保证。

## 致谢

感谢 [MTeam-Genie](https://github.com/astralwaveorg/MTeam-Genie) 的作者与贡献者。本项目参考了其将 M-Team 下载 URL 直接提交给 qBittorrent 的实现思路；此说明不代表双方存在合作、背书或代码授权关系。

### 可选 test2 实站只读验证

`tests/live_mteam_readonly.py` 不在默认单元测试发现范围内，必须手动运行。使用测试站专用 Key，不得使用生产凭据。将以下结构保存为项目目录外、权限 `600` 的 JSON 文件：

```json
{"MTEAM_TEST_API_KEY": "测试站专用值"}
```

```sh
python tests/live_mteam_readonly.py --credentials /安全目录/test-credentials.json --query ubuntu
# 显式覆盖当前支持的全部模式（仅输出计数）
python tests/live_mteam_readonly.py --credentials /安全目录/test-credentials.json --all-modes
```

脚本硬性限定 HTTPS `test2.m-team.cc` 的账户连接、分类、搜索、详情四个端点，禁用 Cookie 和重定向；不生成下载链接、不获取种子、不操作下载器。测试覆盖两页搜索、分类 ID 与精确名称筛选（有对应结果时）。输出仅为计数和验证指标，不记录账户资料、密钥、原始错误或响应。网络失败不会自动重试；任一执行项失败或分类比对不符时退出码为 1，全体执行项成功为 0。无搜索结果时详情和分类筛选不执行，不能将其视为这些接口已验证。测试账户结果不代表生产账户权限。

专用凭据文件由使用者管理，脚本不会删除或自动轮换；保留用于后续联调时应限制访问，停止使用或怀疑泄漏时由使用者在测试站撤销。环境替换不保证保留该文件，不能将其提交仓库或写入公开日志。

验证记录（2026-11-13，test2 专用测试账户）：账户连接成功，实时分类返回 49 项及 5 个分组；Ubuntu 关键词第 1/2 页、资源详情、分类 ID 筛选及实时精确分类名筛选均成功。未获取种子或提交下载；这些结果不代表所有分类、其他账户权限或未来接口行为均已验证。

搜索模式实测（2026-11-13，test2、关键词 ubuntu）：`normal`、`adult`、`movie`、`music`、`tvshow`、`anime`、`rankings`、`all` 返回业务成功；`waterfall` 和 `rss` 均返回 HTTP 200、业务码 1；其中 waterfall 为权限拒绝，rss 的具体原因尚未确认。空结果不表示模式失败。因此本版暂不支持 waterfall 和 rss，工具、配置保存和验证脚本只接受其余 8 种模式，不绕过服务器拒绝、不将失败自动改为其他模式。此结果不能代表所有账号或生产站行为。

旧配置若使用 waterfall 或 rss，网页仍可打开并显示“不支持”的旧值，需明确选择支持的默认模式后保存；插件不会悄悄将原默认值改成 normal。调用时也可明确传入支持的模式覆盖旧默认值。实时分类目录中的同名分组与搜索 mode 不同，不受这一模式范围调整影响。
