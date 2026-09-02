# Youdao X5 OTA lab runbook

本文只描述自有设备隔离网络中的复现路径。先在本地文件中替换以下占位符：

- `{research_host_ip}`：运行 DNS/API/Range 服务的主机地址
- `{device_ip}`：X5 地址
- `{device_id}`、`{device_mid}`：由私有检查请求取得，不提交 Git
- `x5-patched.img`：本地补丁副本，不提交 Git

## 离线门

```sh
python3 -B tools/patch_adb_hash.py --self-test
python3 -B tools/capture_check_version.py --self-test
python3 -B tools/serve_ota_dns.py --self-test
python3 -B tools/serve_custom_ota.py --self-test
python3 -B tools/serve_custom_ota.py --template ota-response.local.json --verify-image
```

最后一项会复读完整镜像和每个 Range 分片。失败时不进入现场步骤。

## 现场门

1. 在私有模板中填写当前设备字段，确认 `readyToServe` 仍为 `false`。
2. 启动仅覆盖 OTA API 域名的 DNS：

   ```sh
   sudo python3 -B tools/serve_ota_dns.py --answer {research_host_ip}
   ```

3. 显式把私有模板的 `readyToServe` 改为 `true`，再启动 API/Range 服务：

   ```sh
   sudo python3 -B tools/serve_custom_ota.py \
     --template ota-response.local.json \
     --bind 0.0.0.0 --public-host {research_host_ip} \
     --api-port 80 --firmware-port 14514 \
     --allow-client {device_ip} --arm-live
   ```

4. 两个服务都打印 `ready` 后，才临时把实验网络 DNS 指向 `{research_host_ip}` 并
   触发检查。

## 停止与恢复

1. `Ctrl-C` 停止两个前台进程。
2. 恢复实验前记录的 DNS；把私有模板的 `readyToServe` 改回 `false`。
3. 确认 UDP 53、TCP 80、TCP 14514 均无监听。
4. 最终只相信设备内摘要与重启后的功能验证，不以下载百分比代替证据。

这些脚本不会安装 daemon、启动项或持久防火墙规则。
