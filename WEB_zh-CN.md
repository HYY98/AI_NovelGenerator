# AI_NovelGenerator Web

Web 界面按原 CustomTkinter 桌面布局迁移，业务实现仍在 `novel_generator/`、`llm_adapters.py`、`embedding_adapters.py`、`consistency_checker.py`。`web_tasks.py` 负责从原配置选择模型并调用这些原函数，不替换原生成算法或提示词。

## 启动

Python 3.10+。卡片、章节和设定文件编辑只需标准库；AI 生成与 WebDAV 使用项目原 `requirements.txt` 依赖。工作区 `.deps` 存在时由服务载入。

```powershell
python web_server.py --port 17777
```

打开 http://127.0.0.1:17777 。也可运行 `start_web.ps1` 并传入 `--port` 参数。只监听本机，不用于公开托管。

## 原界面对应关系

- Main Functions：左侧本章正文、四步生成和批量生成、日志；右侧四个嵌套配置标签、小说参数与可选操作。
- Novel Architecture：原 `Novel_architecture.txt`。
- Chapter Blueprint：原 `Novel_directory.txt`。
- Character State：原 `character_state.txt`。
- Global Summary：原 `global_summary.txt`。
- 章节编辑：原 `chapters/chapter_编号.txt`。
- 设定卡库：地点卡、物品卡、规则设定卡，共用 `setting_cards.json`。
- Other Settings：原 WebDAV 配置与备份、恢复操作。

原 `config.json` 是应用级配置，不随小说目录改变位置。模型选择仍使用 `choose_configs` 中的原阶段选择，Embedding 使用原选择项。Step3 先生成可编辑提示词，确认后生成草稿；Step4 使用当前编辑正文，再调用原定稿流程更新摘要、角色状态与向量库。批量生成按章节顺序调用原草稿和定稿函数。

## 保存与安全

编辑器保存检查文件版本并原子替换；后台生成时阻止并发修改项目，以避免网页与生成线程相互覆盖。外部编辑仍可能使版本失效，此时先保留编辑内容再重新加载合并。手工保存正文不自动更新摘要和向量库，需执行原定稿流程。

API 密钥、WebDAV 密码只在服务端保存，配置读取返回空字段与“已配置”状态。保存时空值表示保留旧密钥，清除需要明确操作。配置损坏时报告错误，不用默认配置静默覆盖。WebDAV 备份和原桌面版一样会向用户配置的服务器上传包含密钥的配置，必须确认目标可信后再操作。

本机服务没有多用户账号权限，不要暴露公网。不要将 `config.json`、日志、小说文件或授权工具提交到仓库。浏览目录与知识库路径均指运行 Python 的电脑，不是远程浏览器的电脑。

## 验证命令

```powershell
python -m unittest discover -s tests -p 'test_web*.py' -v
node --check web/app.js
```

离线测试通过模拟原 API 检查实际参数签名与流程；真实模型质量、网络可达性和远程 WebDAV 服务仍需有效账号及连接验证。
