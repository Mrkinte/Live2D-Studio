# Live2D Studio

编辑 Live2D MOC3 的桌面工具，支持动作合并（按帧采样）、参数默认值修改、点击绑定等功能。

## 文档

- [Live2D Studio](https://mrkinte.github.io/Atlas-Docs/docs/live2d-studio.html)

- [Atlas - 个人NAS导航页](https://mrkinte.github.io/Atlas-Docs/docs/introduction.html)

## 功能

- 设置 Part / ArtMesh 可见性
- 修改 MOC3 参数默认值
- 播放 motion3、绑定声音、按顺序合并并重采样为线性关键帧动作
- 映射`HitAreas`到动作组
- 把参数组合导出为 `.exp3.json`，自动登记到 `model3.json`
- 导出或导入 [Atlas](https://github.com/mrkinte/Atlas) 所需描述文件 `<model>.live2d.json`，配置动作别名、表情、换装组、气泡台词等

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- 支持 Windows / Linux / macOS 

## 安装与运行

```bash
uv sync
uv run live2d-studio
```

## 配置文件

配置保存在用户配置目录，首次运行会自动创建：

- Windows：`%APPDATA%\live2d-studio\config.json`
- macOS：`~/Library/Application Support/live2d-studio/config.json`
- Linux：`$XDG_CONFIG_HOME/live2d-studio/config.json`，未设置时为 `~/.config/live2d-studio/config.json`

```json
{
  "version": 1,
  "appearance": {
    "theme": "dark"
  }
}
```

## 项目结构

```text
src/live2d_studio/
  app.py            # 入口、命令行参数、主题初始化
  config.py         # JSON 配置读写
  core/             # MOC3、model3.json、motion3、参数、清单等无 Qt 逻辑
  ui/               # 主窗口、各面板、预览画布、对话框与主题
tests/              # pytest 测试
assets/             # 发布图标
src/live2d_studio/assets/  # 运行时图标
script/release.py   # 版本与打包入口
live2d-studio.spec  # PyInstaller 配置
.github/workflows/release.yml  # 标签触发的 Windows 发布流水线
```
