# 有道 X5 ADB 鉴权逆向

## 结论

- 固件版本：`3.4.6`；原始镜像 SHA-256：
  `7f653025fb99cdd813f102a4083d67180602d4e1eb78355d17c32ad1641d414f`。
- ADB 密码不是可逆加密，也没有按设备序列号派生：`adb_auth.sh` 对无换行输入计算
  SHA-256，再与脚本末尾固定的 64 位十六进制摘要比较。
- 原厂明文没有恢复。公开仓库不保存后来用于实机验证的自选密码或其目标摘要。

## 鉴权与启动链

固件中的 `/usr/bin/adb_auth.sh` 等价于：

```sh
candidate=$(printf %s "$password" | sha256sum | awk '{print $1}')
expected=$(tail -n 1 /usr/bin/adb_auth.sh | awk -F '# ' '{print $2}')
test "$candidate" = "$expected" && touch /tmp/.adb_auth_verified
```

`adbd` 对 `shell:auth` 启动该脚本；普通 shell/sync 请求先调用 `check`。成功标记位于
`/tmp`，重启后失效。设置层的 `youdao_set_adb(1)` 会重启 SSH 服务、加入
`usb_adb_en` 并重启 USB gadget。

## 固件补丁窗口

- 固定摘要在整包中只出现一次：十进制偏移 `651108823`，十六进制
  `0x26cf21d7`。
- 只替换 64 个 ASCII 十六进制字符，文件长度必须保持 `1,393,242,124` 字节。
- 该窗口位于 100 MiB 分片的第 6 片（从 0 编号，区间
  `629145600..734003200`，尾端为右开）。
- 必须重算第 6 片 MD5、全部分片列表、整包 MD5 和 SHA-256，并同步 OTA 响应。
- `libyd_img.so` 的该路径只观察到 `YDIH` 标记和非零尺寸检查；没有观察到
  payload 签名或 dm-verity/root-hash 绑定。这个静态结论不能替代实机安装验证。

使用 [`tools/patch_adb_hash.py`](tools/patch_adb_hash.py) 从原件生成副本：

```sh
python3 -B tools/patch_adb_hash.py \
  /path/to/original.img /path/to/x5-patched.img \
  --output-template /path/to/ota-response.local.json
```

脚本拒绝覆盖输出、拒绝未知源窗口，密码通过无回显输入读取，原件不修改。

## 验证边界

实机曾完成：自定义 OTA 元数据与 14 个分片被接受、设备自动安装并正常重启、
未认证 shell 继续被拦截、输入本地自选密码后返回 `success.`、随后
`adb shell id` 返回 root。公开仓库只保留复现方法和摘要，不保留设备 IP、MID、
设备 ID、请求签名、密码、私有抓包或补丁镜像。

公开交叉参考：

- <https://github.com/86lbs/ydpen-adb-unlock>
- <https://github.com/orgs/PenUniverse/discussions/277>
- <https://github.com/orgs/PenUniverse/discussions/250>
