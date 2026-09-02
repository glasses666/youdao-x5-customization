# 系统状态 miniapp

有道词典笔 X5（`coco` 平台）的只读状态页。使用固件自带的 `systemInfo` 与
`batteryInfo` QuickJS 模块，不启动服务、不修改系统配置。

构建要求：官方 QuickJS `2020-07-05`，关闭 bignum；以及 `magick`、`zip`、`md5`。

```sh
make CONFIG_BIGNUM= qjsc
QJSC=/path/to/quickjs-2020-07-05/qjsc ./build.sh
```

包 ID：`8090902000000001`，当前版本 `1.0.1`。安装包支持从桌面卸载。
构建产物位于 `dist/system-status.amr`。

脱敏实机渲染证据：[`evidence/system-status-1.0.1-public.png`](evidence/system-status-1.0.1-public.png)。
