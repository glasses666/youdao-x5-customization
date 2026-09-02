# Youdao X5 miniapp maintenance

## 平台事实

- Buildroot `2021.05-rc3`、Linux `4.19.164`、ARMv7 hard-float；不是 Android 用户空间。
- UI 进程 `/usr/bin/miniapp` 使用 QuickJS `20200705` 和 254×800×32-bit 双缓冲 framebuffer。
- 应用位于 `/userdisk/miniapp/data/mini_app/pkg/`；原厂包位于
  `/etc/miniapp/resources/presetpkgs/*.amr`。
- `.amr` 是含 `manifest.json` 和 `*.js.bin` 的 ZIP。
- 本机 Unix socket `/var/run/miniapp-dbg.socket` 提供 install/start/uninstall 路由。

## 可回滚 SSH 维护

把自己的公钥放在可写数据分区，再仅为当前启动 bind mount：

```sh
adb -s {device_ip}:5555 shell \
  'mount | grep -q " on /root " || mount --bind /userdata/maintenance/root /root'
ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes root@{device_ip}
```

公钥可跨重启保留，bind mount 不会。回滚当前挂载：

```sh
adb -s {device_ip}:5555 shell 'umount /root'
```

仓库不包含任何私钥、公钥、设备密码或地址。

## 原生包操作

```sh
curl --unix-socket /var/run/miniapp-dbg.socket \
  -H 'Content-Type: application/json' -X POST http://localhost/install \
  --data '{"path":"/path/to/app.amr"}'

curl --unix-socket /var/run/miniapp-dbg.socket \
  -H 'Content-Type: application/json' -X POST http://localhost/start \
  --data '{"appID":"APP_ID"}'

curl --unix-socket /var/run/miniapp-dbg.socket \
  -H 'Content-Type: application/json' -X POST http://localhost/uninstall \
  --data '{"appID":"APP_ID"}'
```

首次实验前备份 `packages.json`。平台标志不能证明运行兼容性：原厂隐藏 NES 包虽能
安装，却因缺少 `CanvasRenderingContext2D` 在启动时失败；它随后已通过原生包管理器卸载。

## 已验证状态 App

[`system-status-app/`](system-status-app/) 使用原生 `systemInfo` 与 `batteryInfo`，
无需 shell 或常驻服务即可显示电量、充电、存储、型号、版本和 WLAN。包 ID
`8090902000000001`，manifest 声明 `supportUnInstall=true`。

## 60 FPS 与 OTA 门

`greenui::Choreographer` 默认 30 FPS，并读取 `/etc/miniapp/resources/cfg.json` 的
`screen.fps_max`。实机加入 `"fps_max": 60` 后，内存读回 16 ms 帧间隔，切换原厂应用
未出现 swap、ANR 或崩溃。修改只属于当前 A/B 系统槽。

正常 OTA 由 `S50launcher` 经 `guardian_run` 启动 `runOtaMgr`。实机使用
`/userdata/maintenance/disable-ota` 标记阻止该启动项；修改前须备份启动脚本，恢复时
删除标记并把精确的 `/usr/bin/runOtaMgr` 项加回 `/tmp/guard_list`。不要删除
`ota_download` 或 `update_engine` 文件。
