# M-Team + qBittorrent · 0.3.0

Hermes 原生插件

Hermes 原生插件：搜索馒头资源、查看信息，并将用户明确选择的种子提交到 qBittorrent，指定保存目录。**安装后在 Hermes Web Dashboard 的「M-Team 下载」页面完成全部业务配置，不需要手写 JSON、Compose 环境变量或单独安装 Skill。**

仅用于有权获取和传播的资源。插件不提供盗版授权，也不绕过站点账户权限。

## 兼容性与验证范围

- 核实基线：**Hermes 最新正式版 v0.23.1**，发布于 2026-11-10（本次查询时间 2026-11-12）。源码依据该 tag，不是 main。
- Web 扩展：v0.23.1 `dashboard/manifest.json`、FastAPI `router`、Dashboard SDK **1.1.0**。
- 注意：普通 Web Dashboard **不会自动渲染通用 config_schema**；Desktop 的表单不能替代 Web 验证。因此本插件实现了专用 Web 页面，不依赖该自动表单。
- SDK 上游文档仍标 SPIKE：**不保证所有旧版或未来版本兼容**。如果使用别的版本，应核实后再部署。
- 支持 qBittorrent 4.x / 5.x，分别使用 paused / stopped 字段。暂只支持 v1/混合种子，纯 v2 不支持；种子大小上限 8 MiB。
- 已通过：32 项离线 Python 单元/API 测试，JS 语法检查，以及 React + JSDOM 的页面渲染/默认路径编辑/保存请求/密码清空/测试按钮模拟检查。测试覆盖注册、配置校验/脱敏、profile 路径、凭据白名单、API 路由、请求确认、提交查重、超时防重投、qBit 版本参数。
- **尚未完成：用户的真实 Hermes 全进程加载、馒头账户和真实 qBittorrent 端到端联调。**当前执行环境阻止 Chromium 启动，因此未完成真实浏览器视觉验收。源码契约核实、JSDOM 和本地 mock 测试不能替代部署验收。

## 功能

- 馒头关键词搜索、分页、详情、做种数/大小等信息。
- 内部获取下载链接和种子，上传到 qBittorrent，不把含 token/passkey 的链接回传模型。
- 一个默认保存路径；调用可指定其他绝对路径，不指定时使用默认。不限制根路径白名单。
- 默认添加后直接开始下载；网页可勾选“添加后暂停”作首次验收。
- 同一请求一次性消耗；按 info-hash 查重；不移动/重启已有任务；超时不自动重复提交。
- Web 表单：站点地址、qBit 地址、API Key、用户名、密码、默认路径编辑、类别、暂停开关、接口差异高级设置。
- Web 测试按钮：馒头认证+一次搜索；qBit 登录+读取版本。**不获取种子、不添加下载、不验证保存目录写权限。**

## 安装（极空间 Docker / Compose）

### 1. 确认 Hermes 正式版本与持久化目录

使用原来的 Hermes 容器，不要为插件另建一个空 Hermes。先在极空间容器信息确认镜像版本，并在容器终端检查 `hermes plugins --help`。建议按原部署方式固定到 v0.23.1 对应版本，不必为插件开放额外端口。

**插件安装目录是实际 Hermes home 下的 `plugins/mteam-qbit`。**通常为 `~/.hermes`，但容器用户、自定义 `HERMES_HOME` 或 profile 会影响路径。可在容器终端查看：

```sh
id
printf 'HOME=%s\nHERMES_HOME=%s\n' "$HOME" "$HERMES_HOME"
```

在极空间“目录映射”里找到此 home 对应的 NAS 文件夹。以下假设已有 `NAS上的hermes-data → /root/.hermes`；这只是示例，**不代表你的部署一定如此**。

### 2. 放置插件目录

解压包，将 `hermes-mteam-qbit` 里的文件放到 NAS 对应目录，结构如下：

```text
hermes-data/
├── config.yaml                         # 原有 Hermes 配置，保留
└── plugins/mteam-qbit/
    ├── plugin.yaml
    ├── __init__.py
    ├── core.py
    ├── settings.py
    ├── dashboard/
    │   ├── manifest.json
    │   ├── plugin_api.py
    │   └── dist/index.js
    ├── skills/mteam-qbit/SKILL.md
    ├── tests/
    └── README.md
```

不要多套一层解压目录，不要用空挂载覆盖原有 Hermes home。运行用户需能读插件文件，并能在其 home 中写配置和状态。**不需要给 Hermes 挂下载目录，更不需要 docker.sock。**

项目仓库：[cyt-666/hermes-mteam-qbit](https://github.com/cyt-666/hermes-mteam-qbit)。也可使用 Hermes 自带插件安装入口安装此仓库；若页面要求仓库 URL，填写 `https://github.com/cyt-666/hermes-mteam-qbit`。安装后仍需启用插件并刷新/重启以加载页面。

### 3. 启用并重启

可在 Hermes 插件管理页面启用 `mteam-qbit`；若该界面没有发现/启用能力，使用容器终端：

```sh
hermes plugins enable mteam-qbit
```

然后按原部署方式重启 Hermes Gateway。Compose 示例（`hermes` 替换为实际服务名）：

```sh
docker compose restart hermes
```

刷新 Web Dashboard，侧边栏应出现 **M-Team 下载**。安装文件/启用属于部署步骤；**站点、下载器、密钥和目录配置均在下面的网页表单完成**。

### 4. 页面填写并保存

1. **馒头 API 地址**：默认 `https://api.m-team.cc`，以账号当前站点可信 API 说明为准；只允许 HTTPS。
2. **qBittorrent WebUI 地址**：同一 Docker 自定义网络可填 `http://qbittorrent:8080`；否则填 `http://极空间内网IP:WebUI映射端口`。不要填 `localhost`，它在 Hermes 容器里指 Hermes 自己。跨公网建议 HTTPS，勿公网裸露 WebUI。
3. **凭据**：API Key、qBit 用户名/密码直接输入本页。已有值不回显，只显示“已设置”；留空保留已有值，输入新值替换。
4. **默认保存路径**：填写 **qBittorrent 容器内**绝对路径，例如 `/downloads`。不是 NAS 宿主路径，也不是 Hermes 容器路径。实际下载时可另指定 `/media/movies` 等绝对路径；省略则使用此默认值。
5. **类别/暂停**：默认类别 hermes，添加后直接开始；需要暂停验收可勾选。
6. 点击**保存配置**，再分别点**测试馒头**与**测试 qBittorrent**。测试使用已保存配置，不是未保存的输入。

更换服务地址时，已有凭据会用于新地址。保存前核实目标可信。连接测试每个 profile、每项服务限每 15 秒一次。

### 5. 使用与验收

先对 Hermes 说：“查看默认下载路径，不下载。”再测试搜索、详情。最后选一份有权下载的小型测试资源，明确目录后提交，到 qBit 验证实际任务和路径。

无需为此修改 Compose 环境变量。普通配置每次调用重新读取；凭据通过 Hermes 的原生凭据生命周期更新。插件代码更新或首次安装后需重启 Gateway；首次启用后可开新会话确认 Skill 可见。

## Skill 会自动安装吗？

**不需要单独复制或安装。**插件启用加载时调用稳定版的 `ctx.register_skill(...)`，注册包内的 `SKILL.md`，名称为：

```text
mteam-qbit:mteam-qbit
```

上游会将它列入 `skills_list` 和可用技能列表，模型可以通过 `skill_view` 读取。它不是把文本永久注入所有会话，也不等于每次请求一定自动读取全文；可明确让 Hermes “使用 mteam-qbit:mteam-qbit 技能”。停用插件后，该插件 Skill 不再是启用的技能来源。

若之前按 0.1.0 手工复制过 `~/.hermes/skills/mteam-qbit`，可备份后移走旧副本，避免重复和旧规则。**不要误删包内 `plugins/mteam-qbit/skills/`。**

## 工具和日常操作

| 工具 | 参数/作用 | 下载副作用 |
|---|---|---|
| `pt_search` | query，可选 page/limit | 无 |
| `pt_details` | resource_id | 无 |
| `pt_destinations` | 查看默认保存路径 | 无 |
| `pt_prepare_download` | resource_id + destination → request_id/摘要 | 无 |
| `pt_add_download` | request_id + user_confirmed | 添加下载 |
| `pt_download_status` | request_id | 只查状态 |

示例：“搜索 ××，先列结果，不下载。”然后“下载资源 123 到 /downloads/movies”。如果用户已经明确选择资源并要求下载（指定路径或使用默认），不需机械重复确认。仅“搜索一下”不是下载授权。

`user_confirmed` 必须依据真实用户指令，仍遵守 Hermes 自己的审批/权限设置，不要求终端手工批准。

- `present`：任务可见，不代表文件下载完成。
- `submitted_not_yet_visible`：提交已应答但查询尚未见到任务，应继续查状态。
- `unknown`：结果不确定，**不自动重投**，先在 qBit 用 hash 检查。
- 发现相同 hash 已存在：保留原任务路径和状态，不移动、不重启。

## 高级设置

网页的高级区可修改所有对外业务配置，无需写 JSON：

- 下载域名白名单：每行一个精确域名，不含协议/路径/通配符。仅可信域名、HTTPS、443 端口；不跟随跳转。
- 搜索/详情/下载令牌端点、搜索模式。
- 详情编码 form/json；站点要求时 origin 填 DETAIL。
- 请求有效期 60–86400 秒，默认 900 秒。

默认 API：`POST /api/torrent/search` JSON 包含 `keyword`（单数）、mode、pageNumber、pageSize；详情与 `genDlToken` 默认 form id，业务成功码 0。依据公开第三方适配，不是馒头官方长期稳定性承诺。下载响应结构发生变化仍可能需要代码适配。

## 数据存储与安全边界

- **本页是配置的权威入口**，不是 Desktop 的 config_schema 表单。非秘密配置写入当前 profile 的 `mteam-qbit/config.json`，原子替换、0600 权限；状态在同目录 SQLite。
- 凭据通过 Hermes 的 `save_provider_env_credential` 原生生命周期保存，对应 MTEAM_API_KEY / QBIT_USERNAME / QBIT_PASSWORD；不保存到普通 JSON，不回显前端，不返回模型。
- 文件级权限和不回显**不等于静态加密**。Hermes 底层凭据存储方式由你的部署决定，不能宣称本插件额外加密。
- API 挂在 Hermes 自身 `/api/plugins/mteam-qbit/`，使用宿主认证、禁用 gate 和 profile/secret scope，无单独监听端口。前端用 SDK.fetchJSON，不手工读取会话 token；配置/凭据路径每次请求计算，不全局缓存 profile。
- 私密字段只接受固定白名单；空值保留；修改失败可能部分凭据已更新，页面会提示刷新检查。
- 凭据若仍在旧 Compose 环境变量中，应核对 Hermes 凭据优先级，避免旧值覆盖新值；正常 0.3.0 安装不需额外 env_file。
- `user_confirmed` 是模型声明，**不是不可绕过的独立审批系统**。不向陌生人/公共群开放插件或任意 shell；本版没有实现分渠道/分用户授权，也没有额外绕过 Hermes 权限。
- 目标主机为管理员配置，信任 DNS；不是网络沙箱。凭据不跟随下载 URL 转发，带 token 的完整下载链接不暴露给模型。
- 种子提交后可能下载和做种。确认摘要绑定资源 ID/标题/路径而非事先取得的内容哈希，需信任站点不会在两步之间替换内容。

## 排障 / 升级 / 停用

- **网页无入口**：核对实际 profile/home、目录层级、plugin.yaml 与 dashboard/manifest.json；启用后重启并刷新。查看本地日志，勿公开秘密。
- **保存失败**：检查 Hermes 版本、运行用户写权限、合法域名/绝对路径、管理策略是否禁止凭据更新。不要 chmod 777。
- **连接失败**：检查已保存地址、qBit 端口和 Docker 网络；勿通过关闭 qBit 认证来解决。
- **馒头详情失败**：可尝试网页高级区匹配站点要求的 form/json 和 origin；不要盲目放开成功码或主机。
- **下载目录权限不足**：实际写文件的是 qBit 用户，检查 qBit 挂载和权限；连接测试不验证写权限。
- **超时结果 unknown**：先核对任务；仅在确认没有添加后才重新准备一个新请求。
- **从 0.1.0 升级**：先备份配置/状态，替换插件文件。默认 profile 下原来的 `mteam-qbit/config.json` 可继续读取；自定义 `HERMES_PT_CONFIG` 不再作为 Web 配置入口，请在网页重新核对保存。没有自动跨 profile 导入秘密。
- 停用：在 Hermes 插件页停用，或 `hermes plugins disable mteam-qbit`，再按部署方式重启。不会删除 qBit 任务、文件或状态。

## 开发与测试

```sh
python -m unittest discover -s tests -v
node --check dashboard/dist/index.js
# 可选：DOM 模拟测试（开发者环境，非用户安装步骤）
cd tests/ui && npm install --ignore-scripts && npm test
```

核心功能使用 Python 标准库；Web API 使用 Hermes 已有 FastAPI。API 测试需 fastapi/httpx（开发环境依赖，不是要求用户另部署服务）。前端使用宿主 React，不需要用户 npm build。本地测试直接挂载 router，不代表测试了宿主全进程认证；宿主认证/profile 行为通过固定 tag 源码核实。

发布前审查暂存文件和历史。`.gitignore` 排除实际配置、env、SQLite、种子和缓存；忽略规则无法清理已经泄漏到历史的内容。本包没有选择开源许可证，正式开源前由所有者决定。

## 依据

- 正式版：https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1
- Web SDK：https://github.com/NousResearch/hermes-agent/blob/v0.23.1/web/src/plugins/sdk.d.ts
- SDK 认证/profile 请求：https://github.com/NousResearch/hermes-agent/blob/v0.23.1/web/src/lib/api.ts
- API 挂载：https://github.com/NousResearch/hermes-agent/blob/v0.23.1/hermes_cli/web_server_dashboard.py
- 凭据写入：https://github.com/NousResearch/hermes-agent/blob/v0.23.1/hermes_cli/web_routers/config_env.py
- Skill 注册：https://github.com/NousResearch/hermes-agent/blob/v0.23.1/hermes_cli/plugins_content.py
- 馒头第三方适配：https://github.com/pt-plugins/PT-depiler/blob/master/src/packages/site/definitions/mteam.ts
- URL-encoded 实现：https://docs.rs/mediarium-providers/latest/src/mediarium_providers/site/mteam/provider.rs.html
- qBittorrent API：https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29
- 5.0 参数变化：https://github.com/qbittorrent/qBittorrent/issues/21476

## 0.3 保存路径规则与升级

- `pt_prepare_download` 参数为 `resource_id` 和可选 `save_path`。省略 `save_path` 使用网页配置的 `default_save_path`；提供时使用用户指定的 qBittorrent 容器内绝对路径，无需预先添加目录别名。例如 `{"resource_id":"123","save_path":"/media/movies"}`，或 `{"resource_id":"123"}` 使用默认。
- 不实施根目录白名单，也不会在 Hermes 所在容器检查该目录。仅校验绝对 POSIX 路径、长度、控制字符和 `..`，规范化重复斜杠/`.`/尾斜杠；不验证目录存在、可写或符号链接目标。不能据此声称路径隔离安全。qBit 的挂载范围、运行账户权限和 Hermes 的调用者权限才是安全边界；避免特权容器及不必要的可写挂载，只授权可信用户使用提交工具。
- 准备时返回并固定完整路径，提交不再接受修改路径。之后网页默认值变化不会改变已准备请求；想改当前任务目的地需重新准备。已有同 hash 任务只返回其实际路径，不移动、不重启。
- 从 0.2 升级时，为保持可读取旧配置，网页和工具暂取旧 `destinations` 中第一个目录作为默认（没有旧目录则 `/downloads`）。请在网页核对后保存；保存后仅保留单一 `default_save_path`，不再保留别名映射。旧版本已准备但未提交的请求必须重新准备，避免旧语义混用；已提交任务不受影响。
- 安装新文件后重启 Hermes 并刷新页面，确保工具 schema 和 Skill 都更新。不涉及实际下载或自动迁移已有文件。

### 找不到 API Key / qBit 配置入口

工具集（toolset）选择弹窗只控制工具启用，不显示本插件设置。关闭弹窗，回到 **Hermes Web Dashboard** 主侧栏的 **M-Team 下载**；也可在 Plugins 插件管理页进入本插件的 Dashboard 页面。此表单不是聊天工具弹窗、Desktop 通用配置表单，也不是任何第三方 WebUI 都会自动支持的页面。

若没有该入口：
1. 确认运行的是支持 Dashboard 扩展的 Hermes v0.23.1 兼容构建，并且当前管理 Profile 启用了 `mteam-qbit`。工具集可见并不证明 Dashboard 已加载。
2. 重启 **提供网页的 Dashboard 进程/容器**，而非只重启 Telegram Gateway；浏览器强制刷新。若两者在不同容器，Dashboard 也必须能读到插件文件。
3. 核对实际插件目录下同时存在 `dashboard/manifest.json`、`dashboard/dist/index.js`、`dashboard/plugin_api.py`；不要只复制 Python 文件或多嵌套一层目录。使用进程 launch home / 默认 Hermes root 下的 `plugins`；不要只把文件放在任意聊天工作目录。
4. 同一已登录 Dashboard 浏览器的开发者工具 Network 中，检查 `/api/dashboard/plugins` 返回是否包含 `mteam-qbit`（使用页面本身请求，保持当前 Profile 与 URL 前缀）。没有：发现/启用/版本问题；有但 JS 失败：检查其 `/dashboard-plugins/.../mteam-qbit/dist/index.js` 请求；页面出现但读取配置失败：检查 `/api/plugins/mteam-qbit/settings` 与 Dashboard 日志。401 是登录，404 可能是未启用/未挂载/版本不兼容，不能只凭状态码断定。

不要在排查截图或日志里公开密钥、Cookie、Authorization。当前未完成用户实际容器联调，不能把“刷新一下”当成已确认修复。

### 连接测试失败的诊断

测试不会下载。插件显示的错误码用于区分 `credential_missing`（当前 Profile 凭据未读到）、`network`（连接/超时）、`redirect`（拒绝跨地址转发凭据）、`invalid_json`（可能返回网页/防护页）、`http_401`/`http_403`/`http_429` 等 HTTP 状态、`mteam_rejected`（站点业务拒绝/未知结构）、`search_schema`（搜索响应结构不兼容）。这些分类不等于已确认根因，例如 403 不能直接断定 Key 错了。

`api.m-team.cc` 与 `api.m-team.io` 都曾列在站点官方 API 域名公告中。应核对自己站点当前公布的 API 地址及容器可达性，不要把 `kp.m-team.cc` 网页首页直接当 API，也不要随意把凭据发送到未经确认的替代域名。仅提交错误码和非敏感配置帮助排查，不发送 Key、Cookie 或完整响应正文。公告参考：https://t.me/s/M_Team?before=340 。
