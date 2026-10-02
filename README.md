# 专升本语文学习小程序

**当前交付范围已调整为后端服务、Web 管理后台、接口文档及接口联调支持。甲方另行安排学员小程序前端开发。** 仓库内已有 `miniprogram/` 保留为历史原型与交互参考，不作为本阶段前端交付承诺。

题库由甲方按我方规定的格式整理：[题库 Excel 模板](doc/题库导入模板-v1.xlsx)、[整理与交付说明](doc/题库整理与交付说明-v1.md)。甲方前端团队可阅读[后端对接说明](doc/前端团队后端对接说明-v1.md)；其中区分当前可用接口与后续模块。

最新业务规则为：成功购买或激活码兑换后永久开通全题库，不设置 365 天到期；激活码永久可兑换、一码一人一次。商品与永久权益基础服务已实现，支付、兑换仍在后续阶段，具体规格见[后端 RRD](doc/后端RRD-v0.1.md)。

免费试学按文学分类／文言篇目各提供第一页，页内题数由后台配置，切换题型不额外增加免费题量。用户想继续访问受限内容，在小程序内购买永久权益，线上无需扫码。

已有有效永久权益时阻止再次购买或兑换新码，提示“已激活”，新码保持未使用；原成功请求重试不重复发放权益。

这是根据提供的原型图实现的原生微信小程序，使用 JavaScript、WXML、WXSS 和 JSON。项目包含 6 个页面，使用游客 AppID `touristappid`，小程序当前仍使用本地演示数据。项目新增了独立的 Python 题库管理后台，已有独立的微信学员登录、商品与权益查询接口；尚未接入甲方前端、真实支付或激活码。

当前阶段版本为 **v0.2.0**：已完成草稿并发防护、跨平台 CI、微信学员会话、商品与永久权益服务及后台来源撤销；免费试学与激活码正在下一批开发。开发与 PR 状态见 [后端开发阶段与任务](doc/后端开发阶段与任务.md)，接口实施契约见 [共享接口契约](doc/后端共享接口契约-v1.md)。变更范围见 [CHANGELOG](CHANGELOG.md)；版本标签在阶段合并后创建，本版本尚未上线。

## 题库管理后台

后台代码位于 `backend/`，使用 Django、DRF 和 Unfold。支持分类/篇目/题目维护、XLSX 预检导入、草稿发布与下架、带权限的查询接口。首次启动在项目根目录运行：

```powershell
.\backend\setup.ps1
.\backend\start.ps1
```

初始化脚本会让你自行创建管理员账号和密码，然后在浏览器打开 `http://127.0.0.1:8000/admin/`。没有预设管理员密码。题目发布不会改变当前小程序里的演示内容。

详细操作、接口示例和后续扩展边界见 [最小后端使用说明](doc/最小后端使用说明.md)。

| 页面 | 内容 |
| --- | --- |
| 首页 | 示例考试倒计时、学习统计、五个模块入口、继续背诵 |
| 文学常识 | 文学分类、默写／背诵切换、收藏、标记掌握、五题分页 |
| 文言文目录 | 篇目目录、字词／翻译专项、篇目题型选择 |
| 篇目练习 | 重点字词或翻译练习、进度和翻组 |
| 随机练习 | 文常／文言分别随机，每组最多五题，恢复本轮题序 |
| 我的收藏 | 分类查看收藏题、模式切换、取消收藏和分页 |

当前全部页面已应用用户确认的「青墨雅韵」主题：黛绿导航与按钮、纸白背景、琥珀收藏、浅青答案高亮。改造范围和验证边界见 [青墨雅韵 UI 改造说明](doc/青墨雅韵UI改造说明.md)，最新实际页面见 [新版模拟器截图总览](doc/青墨雅韵验收截图/页面总览.png)，检查结果见 [新版验收记录](doc/青墨雅韵UI验收记录.json)。

原始功能范围与阶段任务见 [前端功能与阶段计划](doc/前端功能与阶段计划.md)；原型还原阶段的 [旧截图](doc/验收截图/页面总览.png) 和 [旧验收记录](doc/前端验收记录.json) 作为历史资料保留。

## 导入微信开发者工具

1. 打开微信开发者工具，选择导入小程序项目。
2. 选择**项目根目录**，即克隆仓库后同时包含 `project.config.json`、`miniprogram/`、`doc/` 的目录。
3. 确认项目类型为小程序；保留已有游客 AppID `touristappid`。本地页面演示不要求提供正式 AppID。
4. 导入后点击编译，从首页进入各模块。页面之间使用普通页面导航，本期没有 tabBar。

`project.config.json` 已设置 `miniprogramRoot: "miniprogram/"`。无需选择 `miniprogram/` 子目录重新建项目；原型图片 `ui/`、需求文档 `doc/`、检查脚本及 `.devtools/` 缓存位于包体之外。

直接导入并演示页面不依赖 npm 包构建。下方的 npm 依赖用于开发检查和工具脚本。

## 演示数据和本地记录

当前只有 **16 道演示题**：文学常识 7 题、文言文 9 题。文学题位于先秦文学；文言题包含《劝学》的 4 道字词题、4 道翻译题，以及《师说》的 1 道常识题。目录保留 25 条原型布局，其中部分篇目为待补充占位；没有演示题的分类或篇目会显示空态。这些内容用于演示页面，完整题库及正式答案后续提供。

- 默写版隐藏答案，供脑内回忆；背诵版显示答案，没有输入判分。
- 星标收藏与“标记掌握”独立。首页进度按实际演示题与本地掌握记录计算。
- 收藏、掌握日期、学习日期、模式及继续学习位置保存在 `wx` 本地存储中，存储键为 `chinese-study.frontend.v1`。当前没有跨设备同步。
- 文常与文言随机练习分开；一轮题目先洗牌再分组，每组最多五题，本轮不重复。模式或星标变化不会重新洗牌。
- 示例考试日期为 `2027-03-28`，今日目标为 `20`；日期不代表官方考试安排，两者都可在数据配置中替换。

需要清空演示记录时，可在开发者工具的小程序调试控制台执行以下命令，然后重新编译。它会清除本项目的收藏、掌握和继续学习记录：

```js
wx.removeStorageSync('chinese-study.frontend.v1');
```

## 后续替换题库

主要修改 `miniprogram/data/mock.js`，保留文件最后的导出：

```js
module.exports = { config, categories, articles, questions };
```

各变量的结构如下：

| 变量 | 结构与用途 |
| --- | --- |
| `config` | `examDate` 为 `YYYY-MM-DD` 格式字符串；`dailyTarget` 为每日目标数量 |
| `categories` | 文学分类数组，每项包含 `id`、`name`；当前为 8 个文学分类 |
| `articleRows` | 当前篇目维护入口，每项为 `[篇目ID, 篇名]`；第三项 `true` 表示待补充占位 |
| `articles` | 由 `articleRows` 生成，包含 `id`、`title`、`index`、`placeholder` |
| `questions` | 题目数组；文学题关联分类，文言题关联篇目 |

题目的写法可参考现有字词题：

```js
{
  id: 'word-ri',
  source: 'classical',
  articleId: 'quanxue',
  type: 'word',
  tag: '重点字词',
  stem: '“君子博学而日参省乎己”中，“日”的意思是{{0}}。',
  answers: ['每天']
}
```

- `id` 必须唯一且稳定；收藏、掌握与恢复记录通过它关联。同一道题保留 ID，换成另一道题时使用新 ID。
- `source` 使用 `literature` 或 `classical`。文学题填写 `categoryId`，对应 `categories` 的 ID；文言题填写 `articleId`，对应 `articles` 的 ID。
- `type` 使用 `fact`（常识）、`word`（字词）或 `translation`（翻译）；`tag` 是题卡显示的题型文字。
- `stem` 中的 `{{0}}`、`{{1}}` 依次对应 `answers[0]`、`answers[1]`。不要把答案写进 WXML 模板。
- 文言字词、文言翻译和文言常识的收藏分类由 `data-service` 按题型生成，无需加入文学分类数组。

当前目录布局和项目检查按 25 条原型篇目设计。如需增减篇目或改变分类布局，应同步调整对应页面和检查规则。替换数据后重新编译，并运行下方检查命令；如需从零演示，清空旧本地记录。

## 开发检查命令

在项目根目录打开终端，使用已安装的 Node.js 和 npm：

```powershell
npm install
npm test
npm run check
npm run check:native
```

| 命令 | 用途 |
| --- | --- |
| `npm install` | 安装开发依赖，包括微信小程序自动化工具包；不会增加后端 |
| `npm test` | 运行本地业务测试，验证存储、统计、题目分组等逻辑 |
| `npm run check` | 检查 JS／JSON、页面和组件文件、题目 ID／答案／篇目引用及包体大小 |
| `npm run check:native` | 使用微信开发者工具自带的 `wcc.exe`、`wcsc.exe`，实际编译全部 WXML／WXSS |

原生检查脚本默认检测以下本机安装目录，运行前会确认两个编译器都存在：

```text
C:\Program Files (x86)\Tencent\微信web开发者工具\code\package.nw\node_modules\wcc-exec\wcc.exe
C:\Program Files (x86)\Tencent\微信web开发者工具\code\package.nw\node_modules\wcc-exec\wcsc.exe
```

可在 PowerShell 中自行确认路径：

```powershell
Test-Path -LiteralPath 'C:\Program Files (x86)\Tencent\微信web开发者工具\code\package.nw\node_modules\wcc-exec\wcc.exe'
Test-Path -LiteralPath 'C:\Program Files (x86)\Tencent\微信web开发者工具\code\package.nw\node_modules\wcc-exec\wcsc.exe'
```

如果开发者工具安装在其他位置，将环境变量 `WECHAT_COMPILER_DIR` 设置为同时包含两个编译器的目录，再运行原生检查。例如下面的 `D:` 路径需替换为自己的真实安装位置：

```powershell
$env:WECHAT_COMPILER_DIR = 'D:\微信web开发者工具\code\package.nw\node_modules\wcc-exec'
npm run check:native
```

原生检查结果和编译产物保存于 `.devtools/native-compile/`，不会写入小程序包体。`check:native` 成功表示模板和样式编译成功，不能作为模拟器交互、真机运行或视觉验收通过的证明。实际运行验收记录以阶段文档为准。

`npm run verify:devtools` 是可选的开发工具自动化检查，需要开发者工具已开启自动化服务（默认端口 9420）。该脚本会临时修改演示记录，并尝试在结束时恢复；执行前可自行备份本地记录。自动化结果和截图写入 `.devtools/automation/`，不会覆盖本次交付的人工验收记录和截图。本机游客项目的该流程未通过，曾出现 AppID 工具错误及截图调用超时；本次页面运行检查通过直接操作真实模拟器完成。
