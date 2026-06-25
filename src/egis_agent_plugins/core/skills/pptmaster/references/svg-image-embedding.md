> 通用技术约束见 shared-standards.md。

# SVG 图片嵌入指南

SVG 文件中添加图片的技术规范与工作流。

---

## 图片资源列表格式

在设计规格与内容大纲中定义；每张图片带有 `获取方式` 字段和状态标注。本文件是状态名称和 SVG 嵌入行为的权威来源。若图片方案包含"B) 用户提供"：在八项确认后立即运行 `analyze_images.py` 并在输出设计规格前完成列表。

```markdown
| 文件名       | 尺寸     | 用途      | 类型 | 获取方式    | 状态        |
| ------------ | -------- | --------- | ---- | ----------- | ----------- |
| cover_bg.png | 1280x720 | 封面背景  | 背景 | user        | Existing    |
| team.jpg     | 800x600  | 团队照片  | 摄影 | user        | Existing    |
| chart.png    | 600x400  | 第5页占位 | 插画 | placeholder | Placeholder |
```

### 图片状态枚举

| 状态             | 含义                                    | 执行器处理方式                               |
| ---------------- | --------------------------------------- | -------------------------------------------- |
| **Existing**     | 用户已提供图片（`获取方式: user`）      | 放入 `images/`，用 `<image>` 引用            |
| **Placeholder**  | 故意暂不准备（`获取方式: placeholder`） | 虚线边框占位符；后续替换                     |
| **Needs-Manual** | 获取失败且文件缺失                      | 使用虚线边框占位符（除非用户已手动提供文件） |

---

## 工作流

```
1. 策略师定义图片需求 → 添加图片资源列表，每行含获取方式 + 状态
2. 执行器生成 SVG（svg_output/）
   ├── Existing → <image href="../images/xxx.png" .../>
   └── Placeholder / Needs-Manual（无文件）→ 虚线边框 + 描述文字
3. 预览：python3 -m http.server -d <project_path> 8000 → /svg_output/<filename>.svg
4. 后处理与导出 → 遵循 shared-standards.md §5
```

> 生成阶段在 `svg_output/` 中保持外部引用。`finalize_svg.py` 自动将图片嵌入 `svg_final/`；从 `svg_final/` 导出 PPTX。

---

## 外部引用 vs Base64 嵌入

| 方法            | 优点                   | 缺点                             | 适用                   |
| --------------- | ---------------------- | -------------------------------- | ---------------------- |
| **外部引用**    | 文件小、迭代快、易替换 | 预览需从项目根目录启动 HTTP 服务 | `svg_output/` 开发阶段 |
| **Base64 嵌入** | 自包含文件、导出稳定   | 文件大                           | `svg_final/` 交付阶段  |

---

## 方法 1：外部引用（生成阶段推荐）

### 语法

```xml
<image href="../images/image.png" x="0" y="0" width="1280" height="720"
       preserveAspectRatio="xMidYMid slice"/>
```

### 关键属性

| 属性                  | 说明                   | 示例                        |
| --------------------- | ---------------------- | --------------------------- |
| `href`                | 图片路径（相对或绝对） | `"../images/cover.png"`     |
| `x`, `y`              | 图片左上角位置         | `x="0" y="0"`               |
| `width`, `height`     | 图片显示尺寸           | `width="1280" height="720"` |
| `preserveAspectRatio` | 缩放模式               | `"xMidYMid slice"`          |

### preserveAspectRatio 常用值

| 值               | 效果                           |
| ---------------- | ------------------------------ |
| `xMidYMid slice` | 居中裁剪（类似 CSS `cover`）   |
| `xMidYMid meet`  | 完整显示（类似 CSS `contain`） |
| `none`           | 拉伸填充，不保持宽高比         |

### 预览方法

浏览器安全策略阻止直接打开的 SVG 加载外部图片。从项目根目录启动 HTTP 服务：

```bash
python3 -m http.server -d <project_path> 8000
# 访问 http://localhost:8000/svg_output/your_file.svg
```

---

## 方法 2：Base64 嵌入（交付阶段推荐）

### 语法

```xml
<image href="data:image/png;base64,iVBORw0KGgo..." x="0" y="0" width="1280" height="720"/>
```

### MIME 类型

| MIME 类型       | 文件格式 |
| --------------- | -------- |
| `image/png`     | PNG      |
| `image/jpeg`    | JPG/JPEG |
| `image/gif`     | GIF      |
| `image/webp`    | WebP     |
| `image/svg+xml` | SVG      |

---

## 转换流程

使用 [shared-standards.md §5](shared-standards.md) 中的统一管线。`finalize_svg.py` 在导出前运行，将 `svg_output/` 中的图片引用变为 `svg_final/` 中的嵌入资源。

```bash
python3 scripts/finalize_svg.py <project_path>
python3 scripts/svg_to_pptx.py <project_path>
```

### 独立运行：embed_images.py（高级）

处理特定 SVG 而不走完整管线：

```bash
python3 scripts/svg_finalize/embed_images.py <svg_file>                         # 单文件
python3 scripts/svg_finalize/embed_images.py <project_path>/svg_output/*.svg    # 批量
python3 scripts/svg_finalize/embed_images.py --dry-run <project_path>/svg_output/*.svg  # 预览
```

---

## 最佳实践

### 图片优化

嵌入前压缩以减小文件体积：

```bash
convert input.png -quality 85 -resize 1920x1080\> output.png  # ImageMagick
pngquant --quality=65-80 input.png -o output.png               # pngquant（推荐）
```

### 文件组织

```
project/
├── images/            # 图片素材
├── sources/           # 源文件及附带图片
│   └── article_files/
├── svg_output/        # 原始版本（外部引用）
└── svg_final/         # 最终版本（图片已嵌入）
```

### 圆角 / 非矩形图片裁剪

`<image>` 元素上的 `clipPath` 有条件允许——权威约束见 [shared-standards.md §1.2](shared-standards.md)；此处不重述或放宽。

clipPath 不适用时的兜底：在嵌入前将圆角烘焙进源图片（带 alpha 的 PNG）。

---

## 常见问题

**Q: 直接打开 SVG 看不到图片？**
浏览器安全策略阻止跨目录请求。从项目根目录启动 HTTP 服务，或先运行 `finalize_svg.py` 后从 `svg_final/` 查看。

**Q: Base64 文件太大？**
压缩源文件，使用 JPEG，将分辨率降至实际显示尺寸。

**Q: 如何反向提取 Base64 图片？**
```bash
base64 -d image.b64 > image.png
```
