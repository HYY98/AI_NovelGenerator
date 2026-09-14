# 小说工作台 Web 版

## 启动

需要 Python 3.10 或更新版本。Web 编辑功能只使用 Python 标准库，无需安装桌面版、向量库或模型 SDK。

在项目目录运行：

```powershell
python web_server.py
```

或运行 `powershell -ExecutionPolicy Bypass -File .\start_web.ps1`。

浏览器打开 http://127.0.0.1:8765 。端口被占用时可使用 `python web_server.py --port 8766`。

## 功能与数据

- 章节编辑：新建、加载、保存、删除、查找替换、撤销重做、字数统计、前后章切换。
- 地点卡、物品卡、规则设定卡：结构化编辑、搜索、新建删除、启用停用、JSON 导入导出。
- 项目切换：输入服务器本机的小说目录。默认目录是项目下的 `Novel_Src`。
- 未保存内容会在切换编辑对象或离开页面时提醒；文件被外部修改时阻止旧版本覆盖。

Web 与桌面版共用 `setting_cards.json` 和 `chapters/chapter_编号.txt`，无需转换格式。未保存的浏览器内容不参与桌面版生成；已保存且启用的卡片会由现有桌面生成和审校流程读取。

本次 Web 版本覆盖卡片管理和章节编辑。原桌面版的模型配置、架构生成、批量生成、定稿及向量库管理尚未迁入网页，仍通过 `start_local.ps1` 使用。

## 安全边界

服务仅供本机个人使用，默认监听 `127.0.0.1`。不要直接发布到公网或通过代理开放给不可信用户。当前没有多用户身份认证；项目路径指向服务端本机目录。请求有来源检查与跨页面项目版本校验，但不替代账号权限系统。

请勿将含密钥的 `config.json`、小说正文或卡片库提交到公开仓库。Web 版不提供密码/API 密钥输入，也不会将桌面模型配置返回浏览器。

## 验证

```powershell
python -m unittest discover -s tests -p test_web_server.py -v
```
