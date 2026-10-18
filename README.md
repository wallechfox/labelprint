# LabelPrint · 热敏标签打印服务

一个跑在 Docker 里的轻量标签打印服务：打开网页、填文字或传图片，点一下，热敏标签机就出纸。

用 FastAPI + ESC/POS 指令直接写 USB 打印机（`/dev/usb/lpX`），不依赖 CUPS，专为爱宝 A5870 这类 58mm 热敏标签机设计，也适用于大多数支持 ESC/POS 的机型。

---

## 特性

- 🖨 **文字打印**：靠左 / 居中 / 靠右，字号 1x~6x，**长文本自动折行**，支持中文（GBK）
- 🖼 **图片打印**：旋转、反色、阈值、亮度、对比度、抖动，**网页实时预览所见即所得**
- 🕐 **时间戳**：可加当前年月日时分秒，7 种格式，可放顶部或底部，字号独立
- 👁 **编辑器即预览**：输入框本身就是标签纸，写什么样就打印什么样（自动折行、真实字号、真实对齐一屏可见）
- ✂ **自动切纸**：末尾可留 0~12 行空白（默认 4 行），方便手撕
- 🧹 **一键清空**：编辑区内容一键清空，图片可一键移除
- 🕶 **无痕模式**：文字、图片各有一个开关，勾选后这次打印不写入任何日志
- 🔑 **URL 令牌鉴权**：`?token=xxx`，不知道网址就打不开
- 🐳 **Docker 一键部署**：host 网络 + 设备直通，国内镜像源构建
- 📜 **打印日志**：JSON Lines 落盘，网页可翻页查看、可一键清空，`/logs` 接口分页返回

---

## 快速开始

### 前置条件

- 一台 Linux 主机（NAS / 服务器 / 树莓派均可），已安装 **Docker 与 Docker Compose**
- 一台支持 ESC/POS 的 USB 热敏打印机，已用 `echo "test" > /dev/usb/lp0` 验证能出纸

### 1. 获取代码

```bash
git clone <你的仓库地址> label-print
cd label-print
```

### 2. 确认打印机设备节点

```bash
ls -l /dev/usb/          # 通常是 lp0；多台打印机会有 lp0/lp1/lp2…
lsusb                    # 确认打印机被识别
```

> ⚠️ **接了多台打印机时，`lp0` 不一定是你要的那台**。强烈建议做第 3 步，否则可能打到别的机器上。

### 3.（推荐）用 udev 固定设备名

先查出打印机的 USB ID：

```bash
lsusb
# 例：Bus 001 Device 007: ID 0483:070b STMicroelectronics USB Printing Support
#                             ↑ idVendor:idProduct
```

写规则（把 `0483`、`070b` 换成你自己的）：

```bash
cat > /etc/udev/rules.d/99-label-printer.rules <<'EOF'
SUBSYSTEM=="usbmisc", KERNEL=="lp*", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="070b", SYMLINK+="label-printer", MODE="0666"
EOF

udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=usbmisc
ls -l /dev/label-printer        # 出现即成功
```

> 注意：USB 打印机在 udev 里的子系统是 **`usbmisc`**，不是 `usblp`。规则写错会静默失效（不报错但链接不生成）。

### 4. 生成配置并改令牌

```bash
cp config.env.example config.env

# 改访问令牌（必须！）
sed -i 's|^LABEL_PRINT_TOKEN=.*|LABEL_PRINT_TOKEN=换成你的随机字符串|' config.env

# 设备路径：做了 udev 就填 /dev/label-printer，否则填 /dev/usb/lp0
sed -i 's|^LABEL_PRINT_DEVICE=.*|LABEL_PRINT_DEVICE=/dev/label-printer|' config.env

# 纸宽：58mm 用 384，80mm 用 576
grep -E '^(LABEL_PRINT_TOKEN|LABEL_PRINT_DEVICE|LABEL_PRINT_WIDTH)=' config.env
```

同时把 `docker-compose.yml` 里的设备映射改成同一路径：

```yaml
devices:
  - "/dev/label-printer:/dev/label-printer"
```

### 5. 构建并启动

```bash
docker compose up -d --build
docker logs -f label-print
# 看到 Uvicorn running on http://0.0.0.0:7900 即启动成功
```

`--build` 只在**第一次部署**、以及改了 `Dockerfile` / `requirements.txt` 时需要。之后的日常使用：

| 改了什么 | 命令 |
|---|---|
| `app/` `static/` `scripts/` 里的代码 | `docker restart label-print`（开了 `LABEL_PRINT_RELOAD=true` 连重启都省了） |
| `config.env` | `docker compose up -d` |
| `Dockerfile` / `requirements.txt` | `docker compose up -d --build` |

`docker-compose.yml` 已经把这三个目录挂载进容器，代码不在镜像里，所以改代码**不需要**重新 build。

### 6. 验证

```bash
# 健康检查
curl http://127.0.0.1:7900/health
# {"status":"ok","service":"label-print"}

# 容器里直接打（绕开 API，定位问题最快）
docker exec label-print bash scripts/print_label.sh "测试" "中文OK"
# OK: 已写入 /dev/label-printer
```

浏览器打开：

```
http://<主机IP>:7900/?token=你的令牌
```

> 手机使用建议「添加到主屏幕」，以后一点就开。

---

## 目录结构

```
label-print/
├── Dockerfile                  # 全国内源构建（华为云 SWR + 清华 pip/apt）
├── docker-compose.yml          # host 网络 + 设备直通 + 日志挂载
├── config.env.example          # 配置示例（复制成 config.env 后使用）
├── requirements.txt
├── app/
│   ├── main.py                 # FastAPI 路由 + 鉴权
│   ├── printer.py              # ESC/POS 拼装 + 调脚本 + 日志
├── static/index.html           # 网页打印页
├── scripts/
│   ├── print_label.sh          # 文本打印
│   ├── print_image.sh          # 图片打印（旧接口）
│   └── print_job.sh            # 通用任务打印（写字节流 + 切纸）
└── logs/                       # 日志（挂载到宿主机）
```

---

## 配置参数

复制 `config.env.example` 为 `config.env` 后修改。环境变量在**容器创建时**注入，所以改完要重建容器（`up -d` 检测到 `env_file` 变化会自动重建，不必手动加 `--force-recreate`）：

```bash
docker compose up -d
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `LABEL_PRINT_TOKEN` | — | **必改**。网页 `?token=` 与 API 鉴权 |
| `LABEL_PRINT_PORT` | `7900` | 监听端口 |
| `LABEL_PRINT_RELOAD` | `false` | 开发热重载：`true` 时改代码自动生效，不用重新 build |
| `LABEL_PRINT_DEVICE` | `/dev/usb/lp0` | 打印机设备节点 |
| `LABEL_PRINT_ENCODING` | `gbk` | 中文码页，乱码试 `gb18030` / `utf-8` |
| `LABEL_PRINT_ALIGN` | `0` | 0 左 / 1 中 / 2 右 |
| `LABEL_PRINT_SIZE` | `2` | 字号倍率 1~6 |
| `LABEL_PRINT_FEED` | `4` | 末尾空白行数 |
| `LABEL_PRINT_CHAR_W` | `12` | 半角字符宽（点）= 纸宽 ÷ 一行字符数，换机器要实测 |
| `LABEL_PRINT_CHAR_H` | `24` | 字符高（点） |
| `LABEL_PRINT_LINE_SPACING` | `0` | 额外行距（点）。**0 = 打印机默认行距**，别填 0 点，否则空行完全不走纸 |
| `LABEL_PRINT_MARGIN` | `0` | 左右物理边距合计（点） |
| `LABEL_PRINT_TS_ENABLE` | `false` | 是否默认打印时间戳 |
| `LABEL_PRINT_TS_POS` | `bottom` | `top` 顶部 / `bottom` 底部 |
| `LABEL_PRINT_TS_FORMAT` | `datetime` | 时间格式预设名，见下表 |
| `LABEL_PRINT_TS_SIZE` | `1` | 时间戳字号倍率 |
| `LABEL_PRINT_GAP` | `1` | 文字段与图片段之间空行（仅混排时生效） |
| `LABEL_PRINT_WIDTH` | `384` | 打印宽度（点）：58mm=384，80mm=576 |
| `LABEL_PRINT_THRESHOLD` | `150` | 图片二值化阈值 0~255 |
| `LABEL_PRINT_DITHER` | `false` | `true` 用灰度抖动（照片） |
| `LABEL_PRINT_MAX_IMAGE_MB` | `8` | 图片大小上限 |
| `LABEL_PRINT_MAX_HEIGHT` | `1500` | 图片最大高度（点） |
| `LABEL_PRINT_TIMEOUT` | `60` | 写设备超时（秒） |
| `LABEL_PRINT_LOG` | `/var/log/label-print.log` | 日志路径（`/logs` 已改为分页，不再配置返回条数） |

---

## 网页使用

打开 `http://<主机IP>:7900/?token=你的令牌`，两个区域互不干扰。**排版由服务端计算，预览里看到的就是实际出纸效果**（换行位置、行数、总高度都一致）。

### 文字

**编辑区就是标签纸**——直接在里面打字，看到的就是打印出来的样子：字号、自动折行位置、对齐方式全部真实呈现。

| 控件 / 区域 | 说明 |
|---|---|
| 编辑区 | 直接输入；超宽自动折行，折在哪一个字一眼可见 |
| 灰底行 | 时间戳（不可编辑，可放正文上方或下方） |
| 灰色区 | 手撕留白（不可编辑），会被完整吐出机外 |
| 粉色虚线 | 真正的切纸位置，在留白的**最底部**（虚线以下没有了） |
| 对齐 / 字号 | 靠左 / 居中 / 靠右；1x ~ 6x（2x 为常规），编辑区实时变化 |
| 无痕模式 | 字号右侧复选框，勾选后**本次打印不写入任何记录**（默认不勾） |
| 末尾空白行 | 0~12，默认 4 |
| 清空 / 打印文字 | 编辑区下方并排两个按钮：一键清空内容、提交打印 |
| 状态栏 | 纸宽（如 `58毫米 · 384 点`）、总高度 mm、行数、是否自动折行 |

> 中文输入法（拼音）输入过程不会打断，输入法结束上屏后才重新排版。

### 图片

| 控件 | 说明 |
|---|---|
| 选择文件 | jpg / png / gif / bmp / webp，透明 PNG 自动补白底 |
| 旋转 | 0° / 90° / 180° / 270°（顺时针） |
| 对齐 | 图片宽度不足纸宽时生效 |
| 阈值 | 0~255，越大越白 |
| 亮度 / 对比度 | 0.5~2.0 |
| 照片模式 | Floyd–Steinberg 抖动，照片类勾选 |
| 反色 | 白底黑字 → 黑底白字 |
| 时间戳 | 同样可加在图片上方或下方 |
| 无痕模式 | 对齐右侧复选框，勾选后本次打印不写入记录（与文字区的开关各自独立） |
| 末尾空白行 | 同上 |
| 预览 | 图片 + 时间戳一起显示，与实际出纸一致 |

### 时间戳格式预设

| 预设名 | 示例 |
|---|---|
| `datetime` | 2026-10-17 13:45:02 |
| `datetime_min` | 2026-10-17 13:45 |
| `cn_full` | 2026年10月17日 13:45:02 |
| `cn_short` | 10月17日 13:45 |
| `date` | 2026-10-17 |
| `time` | 13:45 |
| `compact` | 20261017134502 |

> 页面只提供以上 7 种，且编辑区/预览会真实显示效果，选完直接看是否合意。

### 折行规则

- 中文按字断行，英文按单词断，超长无空格串逐字符硬断
- 手动回车强制换行
- **标点避头尾**：`，。、）」` 等不会出现在行首，`（「“` 等不会出现在行尾
- 时间戳与正文之间不留空行
- **自动折行不是真换行**：只有你敲的回车会留在文本里，超宽自动折出来的换行不会。所以改字号后会按新字号重新排版（不会出现"改小了还按大字号折行"）

### 关于切纸与留白

打印顺序：`正文 → 时间戳 → 末尾空白行 → 走纸到切刀 → 切纸`，所以虚线在留白的最底部。

若出纸后最后一行仍藏在机器里没露出来，把「末尾空白行」或 `LABEL_PRINT_FEED` 调大（默认 4 行 ≈ 12mm）。

> 空白行能不能真的走纸，取决于行距指令：程序发的是 `ESC 2`（打印机默认行距）。**如果 `LABEL_PRINT_LINE_SPACING` 被设成某个值时发的是 `ESC 3 n`，n 为 0 点会让空行一步都不走**，所以这个参数保持 `0`（表示用默认行距）最稳。

### 打印记录

页面底部「查看打印记录」可以翻页查看（每页 10 条，最新在前），右边「清空」可一键清空全部记录（只删日志文件，不影响打印机和预览）。

开了「无痕模式」的打印不会出现在记录里 —— 成功、失败都不写。

---

## API

鉴权两种方式任选：URL `?token=xxx`，或请求头 `Authorization: Bearer xxx`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 网页（需 `?token=`） |
| GET | `/health` | 健康检查，无需鉴权 |
| GET | `/config` | 页面默认参数 |
| POST | `/preview` | 排版预览（返回各块几何位置，不打印） |
| POST | `/print` | 纯文字 |
| POST | `/print/image` | 纯图片（旧接口，参数较少） |
| POST | `/print/job` | 通用：文字 / 图片 / 两者 |
| GET | `/logs` | 打印记录，分页：`?page=1&page_size=10` |
| POST | `/logs/clear` | 清空全部打印记录 |

`/print/job` 常用表单字段：`text`、`file`、`align`、`size`、`order`、`feed`、`rotate`、`dither`、`invert`、`threshold`、`brightness`、`contrast`、`ts_enable`、`ts_pos`、`ts_fmt`、`ts_size`、`no_log`（`true` = 无痕，本次不写日志）。

### 示例

```bash
TOKEN=你的令牌
HOST=http://127.0.0.1:7900

# 打印文字
curl -X POST "$HOST/print?token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"李明\n三年级2班\n请假条","align":0,"size":2}'

# 打印图片（旋转 90°、反色、调阈值）
curl -X POST "$HOST/print/job?token=$TOKEN" \
  -F "file=@pic.png" \
  -F "rotate=90" -F "invert=true" \
  -F "threshold=160" -F "brightness=1.1" -F "contrast=1.3" -F "feed=4"

# 文字 + 图片一次出纸
curl -X POST "$HOST/print/job?token=$TOKEN" \
  -F "text=李明 三年级2班" -F "file=@pic.png" -F "order=text-first"

# 查看打印记录（第 1 页，每页 20 条）
curl "$HOST/logs?token=$TOKEN&page=1&page_size=20"

# 清空打印记录
curl -X POST "$HOST/logs/clear?token=$TOKEN"

# 无痕打印：这次不写日志
curl -X POST "$HOST/print/job?token=$TOKEN" -F "text=临时内容" -F "no_log=true"
```

成功：`{"status":"ok","message":"打印成功"}`
失败：`{"status":"error","message":"<具体错误>"}`（HTTP 500）

日志每行一条 JSON：

```json
{"time":"2026-10-17 13:40:02","source":"web","text":"李明\n三年级2班","result":"ok","detail":"OK: 已写入 /dev/label-printer"}
```

`/logs` 返回结构（分页）：

```json
{"status":"ok","page":1,"page_size":10,"total":26,"pages":3,"count":10,"items":[ ... ]}
```

---

## 图片效果调参

| 现象 | 调整 |
|---|---|
| 整体太黑 / 糊成一团 | 阈值调大（180~210） |
| 太淡 / 断线 | 阈值调小（110~140），对比度加到 1.3~1.6 |
| 照片想有层次 | 勾「照片模式」+ 对比度 1.2~1.5 |
| 有摩尔纹 | 关掉「照片模式」 |
| 黑白颠倒 | 勾「反色」 |
| 太小看不清 | 源图宽度建议 ≥ 400px；58mm 纸只有 384 点宽，小图放大必然糊 |

---

## 公网访问（可选）

用 Nginx / Caddy / Lucky 之类反代到 `127.0.0.1:7900` 即可：

```nginx
location / {
    proxy_pass http://127.0.0.1:7900;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

外网入口建议：
- 强制 HTTPS
- 关闭访问日志（URL 里带 token）
- 令牌设长一点（20 位以上随机串）

> 不想暴露公网：用 Tailscale / WireGuard 组内网，或只在局域网用。

---

## 常见问题

### 速查表

| 问题 | 处理 |
|---|---|
| 打印没反应 | `docker exec label-print bash scripts/print_label.sh "测试"`；再看 `docker logs label-print` |
| 打到别的打印机 | 设备号漂移，做 udev 固定名（见快速开始第 3 步） |
| `ERROR: 打印设备不可写` | `docker-compose.yml` 里把 `devices` 换成 `privileged: true` |
| 中文乱码 | `LABEL_PRINT_ENCODING`：`gbk` → `gb18030` → `utf-8` |
| 最后一行卡在机器里 | 调大「末尾空白行」或 `LABEL_PRINT_FEED` |
| 末尾空白行调了完全没反应 | `LABEL_PRINT_LINE_SPACING` 保持 `0`（填 0 点行距会让空行不走纸） |
| 页面 401 / 403 | 网址缺 `/?token=`，或令牌与 `config.env` 不一致 |
| 改了配置没生效 | `docker compose up -d`；改代码不用 build，`docker restart label-print` 即可 |
| 磁盘占用越来越大 | `docker image prune -f` + `docker builder prune -f`（见第 9 条） |
| 构建时拉镜像很慢 | 换基础镜像：`docker compose build --build-arg BASE_IMAGE=docker.xuanyuan.me/library/python:3.12-slim` |
| 脚本报 `bad interpreter` | 文件被存成 CRLF，执行 `sed -i 's/\r$//' scripts/*.sh` |

### 1. 拉取基础镜像极慢

**现象**：`docker compose up -d --build` 长时间停在 `FROM python:3.12-slim`，日志显示几十 KB/s。

**原因**：直连 Docker Hub 慢。

**处理**：项目已默认使用国内源，仍慢可换源（不必改文件）：

```bash
docker compose build --build-arg BASE_IMAGE=docker.xuanyuan.me/library/python:3.12-slim
```

可选地址：`swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.12-slim`、`docker.1ms.run/...`、`hub.rat.dev/...`。

判断是不是真卡死：进度里的 MB 数字还在涨就没死，中断重来会复用已下载的层。

### 2. 打印打到了另一台打印机

**现象**：目标打印机没反应，另一台打印机出纸了。

**原因**：设备号会随插拔顺序、重启变化，后插入的可能拿到 `lp1`，而配置里写的是 `lp0`。

**定位**：

```bash
ls -l /dev/usb/
lsusb          # 例如 0483:070b = 爱宝 A5870，04f9:0033 = Brother HL-2140
```

**根治**：用 udev 按 USB ID 固定名字（见快速开始第 3 步）。

### 3. udev 规则写了却不生成符号链接

**现象**：`udevadm trigger` 无报错，`/dev/label-printer` 却不存在。

**原因**：USB 打印机在 udev 里的子系统是 **`usbmisc`**，不是 `usblp`，规则没匹配上。可用这条确认：

```bash
udevadm info -q all -n /dev/usb/lp1 | head -15
# U: usbmisc
# P: /devices/.../usb1/1-2/1-2:1.0/usbmisc/lp1
```

**正确写法**：

```
SUBSYSTEM=="usbmisc", KERNEL=="lp*", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="070b", SYMLINK+="label-printer", MODE="0666"
```

调试用（注意要传真实 sysfs 路径，传 `/sys/class/...` 会报 `No such device`）：

```bash
udevadm test /sys/devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2:1.0/usbmisc/lp1 2>&1 | grep -Ei 'label-printer|SYMLINK|ATTRS'
```

### 4. 改了配置却没生效

**原因**：环境变量在**容器创建时**注入，改文件不会同步进运行中的容器。

```bash
docker exec label-print printenv | grep -E 'LABEL_PRINT_TOKEN|LABEL_PRINT_DEVICE'   # 看到的还是旧值
docker compose up -d              # 只改配置（env_file 变了会自动重建容器）
docker restart label-print        # 只改了 app/ static/ scripts/ 里的代码
docker compose up -d --build      # 改了 Dockerfile / requirements.txt
```

> 改代码**不需要** `--build`：`docker-compose.yml` 已经把 `app/`、`static/`、`scripts/` 挂载进容器，镜像里那份不参与运行。开 `LABEL_PRINT_RELOAD=true` 的话连 `restart` 都省了。

另一个隐蔽原因：文件被存成 CRLF，值末尾多了 `\r`，比对永远失败。用 `cat -A config.env | grep TOKEN` 检查（结尾应是 `$`，`^M$` 就说明有 CR）。

### 5. 请求到底有没有到达服务

**现象**：外网 / 第三方回调失败，不确定是服务端问题还是网络问题。

**判断**：直接看容器访问日志：

```bash
docker logs --tail 80 label-print | grep -E 'GET|POST'
```

- 有请求、返回 4xx/5xx → 服务端问题（鉴权、参数等）
- **一条都没有** → 请求没进来，属于网络/域名/证书问题，改代码没用

常见原因：域名只解析到 IPv6 而对方走 IPv4、没有公网入口、HTTPS 证书链不完整、反代规则没覆盖对应路径。

```bash
python3 -c "import socket;print(socket.getaddrinfo('<域名>',443))"   # 看有没有 A 记录
curl -4 -s -o /dev/null -w "IPv4: %{http_code}\n" https://<域名>/health
curl -6 -s -o /dev/null -w "IPv6: %{http_code}\n" https://<域名>/health
```

### 6. 最后一行打不出来 / 末尾空白行调了没反应

**原因通常有两个**：

1. 正文结束立刻走纸切刀，最后一行还留在机头里没送出来
2. 行距指令被设成了 0 点（`ESC 3 0`）——这种机器文字行按字符高度自动走纸，但**空行只靠行距走纸**，行距为 0 就一步都不走，末尾空白行设几都一样

**处理**：

```bash
# 行距保持 0（0 = 用打印机默认行距 ESC 2，空行才会走纸）
grep LABEL_PRINT_LINE_SPACING config.env
```

再把「末尾空白行」或 `LABEL_PRINT_FEED` 调大（默认 4 行 ≈ 12mm，不够就 6~8 行）。

### 7. 图片「有轮廓但效果不好」

见上文「图片效果调参」。关键：网页预览与服务端 `build_image_bytes()` 算法一致（亮度 → 对比度 → 反色 → 二值化），调完直接打即可。

位图结构参考：384×192 的图 → 头部 `1d 76 30 00 | 30 00 | c0 00`，数据长度 = 8 + 48 × 192 字节。

### 8. 通用定位顺序

打印链路是：**网页 → API → printer.py → bash 脚本 → 设备**，逐层验证：

```bash
# 第 1 层：服务活着吗
curl http://127.0.0.1:7900/health

# 第 2 层：容器能写设备吗（绕开所有业务代码，最快定位）
docker exec label-print bash scripts/print_label.sh "测试" "中文OK"

# 第 3 层：API 通吗
curl -X POST "http://127.0.0.1:7900/print?token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"text":"测试"}'

# 第 4 层：看结果
tail -5 logs/label-print.log
```

第 2 层能出纸、第 3 层不出 → 问题在 API/参数；第 2 层就不出 → 问题在设备或脚本。

### 9. 反复 build 之后磁盘占用变大

**现象**：`docker compose up -d --build --force-recreate` 用多了，磁盘越来越满。

**原因**：镜像本身大小是固定的（约 200MB），累积的是两样东西：

- 旧镜像没了 tag 变成 `<none>` 悬空镜像
- BuildKit 构建缓存（每次 `COPY app` 那层变了都会留一份旧层）

**查看**：

```bash
docker system df
docker images | grep -E 'label-print|python'
```

**清理**：

```bash
docker image prune -f                          # 删悬空镜像，安全
docker builder prune --keep-storage=2GB -f     # 构建缓存只留 2GB
```

别用 `docker system prune -a`：它会删掉所有没被容器用到的镜像，别的服务可能要重新拉。

**从根上避免**：改 `app/` `static/` `scripts/` 不需要 `--build`（目录已挂载），改 `config.env` 用 `docker compose up -d`，只有 `Dockerfile` / `requirements.txt` 变了才需要构建。

---

## 安全提醒

- **务必修改默认令牌**，不要把 `config.env` 提交到仓库（已在 `.gitignore` 中）
- 令牌出现在 URL 中，会被浏览器历史、代理日志记录；公网部署请关闭反代访问日志
- 服务**没有做限流**，公网暴露时建议加一层访问控制（IP 白名单 / Basic Auth）

---

## 已知限制

- **图文不能左右并排**：ESC/POS 是流式协议，位图指令会换行。要做 Word 式自由排版只能整页渲染成位图，代价是需要装中文字体且文字清晰度下降。当前设计为文字、图片各自独立打印。
- 图片分辨率受热敏头物理限制（58mm 仅 384 点宽）。
- 只在 Linux + Docker 上验证过，未适配 CUPS 队列打印。

---

## 更新日志

- **v1.1（当前）**：首个正式发布版本。文字打印（自动折行 + 标点避头尾 + 时间戳 + 字号 1~6x）、图片打印（旋转 / 反色 / 阈值 / 抖动）、编辑区即预览（所见即所得）、无痕模式、打印记录分页与清空、URL 令牌鉴权、Docker 部署与热重载；并修复末尾回车不生效、快速输入字符乱序、Home/End 与上下键导航失灵、输入字符跳动等编辑器问题

---

## 技术栈

Python 3.12 · FastAPI · Uvicorn · Pillow · Bash(ESC/POS) · Docker Compose

基础镜像默认使用华为云 SWR 的 DockerHub 同步源，pip / apt 使用清华源，国内构建无需科学上网。

---

## License

MIT
