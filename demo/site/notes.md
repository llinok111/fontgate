# 交付前检查清单

## 为什么需要闸门

字体子集化是一个**不可逆的破坏性操作**。它删掉字形、裁掉表、重排索引，然后告诉你成功了。
成功只说明文件写出来了，不说明这个文件还能渲染你要的文字。

### 三个真实故障

1. **静默丢字**：`pyftsubset` 的 `--ignore-missing-unicodes` 默认开启，字库里没有的字符会被直接跳过，退出码仍然是 0。
2. **扩展名撒谎**：不传 `--flavor=woff2` 时，输出是裸 sfnt，但如果文件名写成 `.woff2`，浏览器会拒绝加载。
3. **字重漂移**：微软雅黑的 Regular 有 29905 个码位，Bold 只有 29888 个。中间那 17 个字在加粗时会换字体。

### 检查项

| 检查 | 判据 | 失败后果 |
| --- | --- | --- |
| coverage | 期望字符 ∈ 子集 cmap 且有轮廓 | 页面出现豆腐块或回退字体 |
| metrics | advance width 与 bbox 不变 | 整行位移、标点挤压 |
| layout | GSUB/GPOS 引用不悬空 | 连字断裂、变音符错位 |
| format | magic bytes 与扩展名一致 | OTS 解析失败，字体完全不加载 |
| family | 同族同字重 cmap 一致 | 粗体跳字 |

> 覆盖率的分子分母都要能追溯到具体文件与行号，否则修不动。

## 常用命令

```bash
fontgate audit --font build/zh.woff2 --text src/
fontgate verify --source zh.ttf --subset build/zh.woff2 --expect-file src/ --budget 120kb
fontgate family --font zh-regular.woff2 --font zh-bold.woff2
```

温度 25°C，湿度 60%，尺寸 12pt–48pt。
