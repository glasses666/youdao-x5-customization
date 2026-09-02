# Youdao X5 customization toolkit

有道 X5 词典笔自定义与维护工具集，包含固件分析、ADB 鉴权、OTA 协议和原生
miniapp 的可复现实现路径。

本仓库只包含自行编写的工具、脱敏协议样例、文档和 miniapp；不包含厂商固件、
rootfs、抓包、设备标识、密码、SSH 密钥、请求签名或访问令牌。

## 已验证结论

- 固件外层是 `YDIH` 容器，包含 A/B boot/system 布局；原始镜像大小为
  `1,393,242,124` 字节，SHA-256 为
  `7f653025fb99cdd813f102a4083d67180602d4e1eb78355d17c32ad1641d414f`。
- 用户空间是 Buildroot `2021.05-rc3`、Linux `4.19.164`、ARMv7 hard-float，
  不是 Android 用户空间。
- ADB 二次鉴权比较固件内固定的无盐 SHA-256；已定位 64 字节替换窗口，详见
  [`ADB_AUTH_ANALYSIS.md`](ADB_AUTH_ANALYSIS.md)。
- UI 是 QuickJS `20200705` 驱动的 `miniapp`；`.amr` 是带 manifest 和字节码的
  ZIP 包。仓库包含已在 X5 实机运行的只读
  [`system-status-app`](system-status-app/)。
- OTA 检查和 100 MiB HTTP Range 分片协议已完成离线与实机验证；公开模板默认
  `readyToServe=false`，且所有设备字段均为占位符。

## 目录

| 路径 | 内容 |
| --- | --- |
| [`tools/patch_adb_hash.py`](tools/patch_adb_hash.py) | 原件不动地生成补丁副本、整包与分片摘要 |
| [`tools/capture_check_version.py`](tools/capture_check_version.py) | 捕获一次 OTA 检查并只返回“无更新” |
| [`tools/serve_ota_dns.py`](tools/serve_ota_dns.py) | 仅覆盖 OTA 域名的 DNS 响应器 |
| [`tools/serve_custom_ota.py`](tools/serve_custom_ota.py) | 带关闭门、客户端白名单和 Range 校验的离线服务器 |
| [`ota-response-template.json`](ota-response-template.json) | 脱敏线格式与已验证分片边界 |
| [`MINIAPP_MAINTENANCE.md`](MINIAPP_MAINTENANCE.md) | miniapp、SSH、60 FPS 和 OTA 安全开关记录 |
| [`launcher-design/`](launcher-design/) | 800×254 自定义桌面视觉稿，未部署 |
| [`gpt-pen/bridge.py`](gpt-pen/bridge.py) | 本机令牌验证的 X5→Codex 文本/图片桥原型 |

## 快速自检

```sh
python3 -B tools/patch_adb_hash.py --self-test
python3 -B tools/capture_check_version.py --self-test
python3 -B tools/serve_ota_dns.py --self-test
python3 -B tools/serve_custom_ota.py --self-test
python3 -B gpt-pen/bridge.py --self-test
```

这些自检只使用回环地址和临时合成数据，不访问设备、不修改 DNS、不启动持久服务。

## 复现路径

1. 自行从拥有的设备或厂商渠道取得固件，并核对上述大小与 SHA-256。
2. 运行 `patch_adb_hash.py` 生成新副本和本地 OTA 模板；密码由 `getpass` 读取，
   不会写入命令行或输出文件。
3. 用 `capture_check_version.py` 在隔离网络中取得本机设备字段；输出目录已被 Git 忽略。
4. 先运行 `serve_custom_ota.py --verify-image` 完成全镜像/分片复读，再按
   [`LIVE_OTA_RUNBOOK.md`](LIVE_OTA_RUNBOOK.md) 的双门禁流程进行自有设备测试。
5. 重启后以设备内目标哈希和实际 `adb shell` 结果作为最终证据。

研究对象和系统文件归原权利人所有。本仓库没有附加开源许可证；在明确许可证前，
代码默认保留所有权利。
