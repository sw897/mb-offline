# mb-offline

Mapbox 矢量瓦片离线命令行工具，将指定区域下载为 Mapbox SQLite 缓存，并按需将矢量瓦片导出为 MBTiles。

A command-line tool for downloading Mapbox vector tiles within a specified
region to a Mapbox SQLite cache, with optional export to MBTiles.

本项目是基于固定上游版本维护的独立兼容工具，与 Mapbox 官方无隶属关系。访问托管地图资源、离线存储及再分发的权限，取决于适用的服务条款和账户协议。软件许可证不授予 Mapbox 数据的使用权。本仓库不包含地图数据或凭据。

This is an independent compatibility tool maintained against a pinned upstream
version and is not affiliated with Mapbox. Access to hosted map resources,
offline storage and redistribution depend on the applicable service terms and
your account agreement. The software license does not grant rights to Mapbox
data. This repository contains no map data or credentials.

## 环境与状态 / Requirements and status

- 下载脚本和导出工具需要 Python 3.9 或更新版本。

  Python 3.9 or newer is required for the download wrapper and exporter.

- 构建和运行原生下载器需要支持 BuildKit 的 Docker。

  Docker with BuildKit is required for building and running the native downloader.

- 请求 Mapbox 托管资源需要适用的 Mapbox access token。

  A suitable Mapbox access token is required for requests to Mapbox-hosted resources.

- CI 已配置使用 Linux amd64 和 arm64 原生运行器构建镜像。Docker 镜像仅适用于 Linux，Python 脚本在宿主机运行。

  CI is configured to build on native Linux amd64 and arm64 runners. The
  Docker image is Linux-only; the Python scripts run on the host.

- 当前兼容镜像仍使用 Ubuntu 20.04 Focal，其标准安全维护[已于 2025 年 5 月结束](https://ubuntu.com/20-04)。本项目未配置 Ubuntu Pro/ESM，不承诺获得当前安全更新。迁移到仍受维护的基础镜像时，需要同时验证旧版 C++ 代码和运行时 ABI 的兼容性。

  The compatibility image still uses Ubuntu 20.04 Focal. Its standard security
  maintenance [ended in May 2025](https://ubuntu.com/20-04). This project does
  not configure Ubuntu Pro/ESM or promise current security coverage. Migration
  to a maintained base requires testing the legacy C++ code and runtime ABI.

## 构建 / Build

下载脚本默认使用 `mbgl-offline`（等同于 `mbgl-offline:latest`），与下面的构建命令使用同一镜像名称。

The download wrapper defaults to `mbgl-offline` (equivalent to
`mbgl-offline:latest`), matching the image name used in the build commands below.

```sh
docker build --tag mbgl-offline .
```

Dockerfile 采用两阶段构建。最终镜像包含可执行文件、运行库和许可声明，默认以 UID/GID `65532:65532` 运行。基础镜像摘要和原生源码提交均已固定；apt 仓库未使用快照，因此不保证每次构建的产物逐字节一致。

The Dockerfile has two stages. The final image contains the executable,
runtime libraries and license notices; it runs as UID/GID `65532:65532`.
The base image digest and native source revision are pinned. Apt repositories
are not snapshotted, so this is not a promise of byte-identical builds.

在本地构建 arm64 镜像时，如果 Ubuntu 官方 ports 镜像较慢，可以指定阿里云镜像：

For a local arm64 build where the official Ubuntu ports mirror is slow:

```sh
docker build \
  --build-arg APT_MIRROR=http://mirrors.aliyun.com/ubuntu-ports \
  --tag mbgl-offline .
```

构建 amd64 镜像时，阿里云地址应使用 `/ubuntu`，而非 `/ubuntu-ports`。未指定覆盖值时，使用 Ubuntu 官方仓库。

For amd64, an Aliyun mirror override must use its `/ubuntu` repository
instead of `/ubuntu-ports`. The default uses the official Ubuntu repositories.

### 瓦片数量上限 / Tile count limit

默认构建保留上游的 **6000 瓦片**离线上限。这是原生缓存中 Mapbox 瓦片的累计上限；估算值低于 6000 也不代表一定不会触及限制，因为样式可能包含多个数据源，原生数据源层级也可能与请求网格不同。

The default build preserves the upstream **6000-tile** offline limit.
This is the native cache's cumulative Mapbox tile limit, not a guarantee that
every estimate below 6000 will fit: styles may have multiple sources, and
native source zooms can differ from the requested grid.

在适用的账户条款允许预期用途的前提下，可以通过调整显式构建参数实现更大离线缓存，上限为 `2147483647`：

If the applicable account terms permit your intended use, adjust the explicit
build argument to allow a larger offline tile cache, with a maximum tile count
of `2147483647`:

```sh
docker build \
  --build-arg OFFLINE_TILE_COUNT_LIMIT=12000 \
  --tag mbgl-offline:custom .
```

下载时使用 `--image mbgl-offline:custom` 选择该镜像。修改客户端常量不会改变服务配额、速率限制、计费或数据权利。实际限制取决于运行的镜像；已有镜像保留其编译时的上限，重新构建或重新打标签可能改变本地标签所指向的镜像。配置值记录在镜像标签 `org.mb-offline.offline-tile-count-limit` 中。

Use `--image mbgl-offline:custom` with the wrapper for that image. Changing this
client-side constant does not change service quotas, rate limits, billing or
data rights. The effective limit is determined by the image being run. Existing
images keep their compiled limit; rebuilding or retagging can change which
image a local tag selects. The configured value is recorded in the image label
`org.mb-offline.offline-tile-count-limit`.

## 离线预览下载 / Preview a download without network access

```sh
python3 scripts/download.py \
  --bbox 116.38,39.90,116.42,39.94 \
  --min-zoom 11 --max-zoom 12 \
  --output data/map.db \
  --dry-run
```

`--dry-run` 会输出瓦片网格估算和不含 token 值的命令，不会调用 Docker、下载资源或创建输出数据库。估算统计的是空间网格单元，不会枚举样式的全部资源，也不能精确预测存储空间或最终请求次数。

`--dry-run` prints the tile-grid estimate and a command without a token value.
It does not invoke Docker, download resources or create the output database.
The estimate counts spatial grid cells; it does not enumerate every style
resource, predict storage precisely or determine the final request count.

## 凭据与下载 / Credentials and download

未设置凭据环境变量且没有配置文件时，在终端运行以下命令会提示输入 token，输入内容不会回显：

If no credential environment variable or configuration file is present,
running the following command in a terminal prompts for a token without echoing it:

```sh
python3 scripts/download.py \
  --bbox 116.38,39.90,116.42,39.94 \
  --min-zoom 11 --max-zoom 12 \
  --output data/map.db
```

下载脚本按以下优先级读取凭据：

The wrapper checks for credentials in this order:

1. 环境变量 `MAPBOX_ACCESS_TOKEN`。

   `MAPBOX_ACCESS_TOKEN` in the environment.

2. `--env-file` 指定的文件；未显式指定文件时，使用本地 `.env`。

   The file selected with `--env-file`, or the local `.env` when no explicit
   file is selected.

3. 不回显输入内容的交互提示。

   A hidden interactive prompt.

如需文件配置，将 `.env.example` 复制为 `.env`，并在本地填写 token。Git 会忽略 `.env`，Docker 构建上下文也会排除它。请勿将真实 token 放入命令参数、截图、issue、提交或示例中；已暴露的 token 应及时轮换。

For file-based configuration, copy `.env.example` to `.env` and fill in your
token locally. `.env` is ignored by Git and excluded from the Docker build
context. Do not put real tokens in command arguments, screenshots, issues,
commits or examples. Rotate any exposed token.

下载脚本通过环境变量向 Docker 传递 token。在 POSIX 宿主机上，脚本会映射调用者的 UID/GID，确保挂载的输出目录可写，且生成文件归调用者所有。

The wrapper passes the token to Docker through an environment variable. On
POSIX hosts it maps the calling user's UID/GID so the mounted output directory
remains writable and output files belong to that user.

`--yes` 只跳过交互确认，不会提供凭据。`--overwrite` 会替换指定的输出数据库及其 SQLite 旁文件；未指定该参数时，保留已有输出。下载不会自动断点续传。

`--yes` skips the interactive confirmation; it does not supply credentials.
`--overwrite` replaces the selected output database and its SQLite sidecars.
Without that flag, an existing output is preserved. Downloads are not resumed
automatically.

### GeoJSON 输入 / GeoJSON input

```sh
python3 scripts/download.py \
  --geojson tests/fixtures/beijing.geojson \
  --min-zoom 11 --max-zoom 12 \
  --output data/region.db \
  --dry-run
```

下载脚本接受单个 Feature 或 geometry，支持点、线、面及递归嵌套的 GeometryCollection，不接受 FeatureCollection。集合中的每个几何成员都会参与包围盒计算；不支持的几何类型、缺失的坐标数组或越界坐标会报错，不会跳过该成员。原生下载器仍按原始几何区域处理。

The wrapper accepts a single Feature or geometry, including points, lines,
polygons and recursively nested GeometryCollections. It rejects
FeatureCollection. Every geometry member contributes to the bounding box;
unsupported geometry types, missing coordinate arrays and out-of-range
coordinates are rejected rather than skipped. The native downloader still
applies the original geometry.

GeoJSON 使用包围盒上界估算，并计入东侧和南侧边界上端点所在的网格，避免点或线落在瓦片边界时被估算为 0。例如 `Point(0,0)` 在 z11–12 每层各计 1 个网格，共 2 个。该上界可能高于相同范围的普通 bbox 估算；普通 bbox 保持原来的边界规则。

GeoJSON uses a bounding-box upper bound that includes cells containing
endpoints on its east and south edges, so points or lines on tile boundaries
are not estimated as zero. For example, `Point(0,0)` counts one cell at each
of z11 and z12, two in total. This upper bound can exceed the estimate for an
equivalent area bbox; ordinary bbox boundary rules are unchanged.

移除 `--dry-run` 后即可进行需要确认的下载。其他选项包括 `--style`、`--pixel-ratio`、`--image` 和 `--api-base-url`；完整说明见 `python3 scripts/download.py --help`。

Remove `--dry-run` to perform a confirmed download. Other options include
`--style`, `--pixel-ratio`, `--image` and `--api-base-url`; run
`python3 scripts/download.py --help` for details.

下载脚本会拒绝跨越日期变更线的包围盒、超出 Web Mercator 范围的纬度，以及超出 0–22 的层级范围。高于 16 的层级可能只是对已有数据进行过度缩放，不会增加源数据细节。

The wrapper rejects bounding boxes that cross the antimeridian, out-of-range
Web Mercator latitudes and zoom ranges outside 0-22. Zooms above 16 can overzoom
a source without adding source detail.

### CJK 字形 / CJK glyphs

如需将中文、日文和韩文字形资源包含在离线缓存中，添加 `--include-ideographs`。预览和实际下载应保留相同的选项；字形资源会增加下载内容，但不会改变空间瓦片网格的估算。

Add `--include-ideographs` to include Chinese, Japanese and Korean glyph
resources in the offline cache. Keep the option in both preview and actual
download commands. Glyph resources add to the download without changing the
spatial tile-grid estimate.

```sh
python3 scripts/download.py \
  --bbox 116.38,39.90,116.42,39.94 \
  --min-zoom 11 --max-zoom 12 \
  --include-ideographs \
  --output data/map-cjk.db \
  --dry-run
```

## 导出 MBTiles / Export MBTiles

```sh
python3 scripts/export_mbtiles.py data/map.db data/map.mbtiles
```

导出器支持 Mapbox GL Native schema-v5 和 schema-v6 离线缓存，会检查 `PRAGMA user_version`、必需表及导出所需字段，拒绝其他版本或结构不兼容的数据库。v6 相比 v5 仅在 `resources`、`tiles` 表新增 `must_revalidate` 字段，导出过程不使用该字段，因此无需升级或修改 v5 源库。

The exporter supports Mapbox GL Native schema-v5 and schema-v6 offline caches.
It checks `PRAGMA user_version`, required tables and columns needed for export,
and rejects other versions or incompatible structures. Compared with v5, v6
only adds `must_revalidate` to `resources` and `tiles`. Export does not use
that field, so a v5 source does not need to be upgraded or modified.

导出前必须停止源库写入，且源文件不能带有 `-journal`、`-wal` 或 `-shm` 旁文件。应先安全恢复被中断的 SQLite 数据库，请勿通过删除旁文件强行转换。源文件以只读、immutable 模式打开；目标文件在目标目录下独立的临时目录中生成，通过原子替换就位，成功或失败后均会清理本次临时输出。

Stop source writers before exporting. The source must have no `-journal`,
`-wal` or `-shm` sidecar. Safely recover an interrupted SQLite database first;
do not delete a sidecar to force conversion. The source is opened read-only
and immutable. Output is built in a unique temporary directory within the
destination directory and atomically installed. Temporary output from this
run is cleaned up on success or failure.

输入和输出必须指向不同文件。相同路径、等价路径、符号链接或硬链接指向同一文件时，即使使用 `--overwrite` 也会被拒绝。仅在确定替换另一个已有目标文件时使用 `--overwrite`。SQLite URI 会正确编码路径中的空格、中文及 `#`、`?`、`%` 等字符；在命令中仍应给路径加引号，例如：

Input and output must refer to different files. Identical or equivalent paths,
symbolic links and hard links that refer to the same file are rejected even
with `--overwrite`. Use that flag only when replacing a distinct existing
destination is intended. SQLite URI paths correctly encode spaces, Unicode
and characters such as `#`, `?` and `%`; still quote paths in shell commands:

```sh
python3 scripts/export_mbtiles.py "data/区域 #1.db" "data/区域 #1.mbtiles"
```

导出器将 XYZ 的 `y` 坐标翻转为 MBTiles 的 TMS 行号，跳过空瓦片记录，写入 gzip 压缩的 PBF，并推导边界、层级和矢量图层元数据。跨数据源或像素比的重复坐标，以及无效瓦片载荷会被拒绝。标准 MBTiles 坐标查询使用唯一索引。

The exporter converts XYZ `y` coordinates to MBTiles TMS rows, skips null tile
records, writes gzip-compressed PBF, and derives bounds, zooms and vector-layer
metadata. It rejects duplicate coordinates across sources/pixel ratios and
invalid tile payloads. Standard MBTiles coordinate lookups use a unique index.

归档包含瓦片和元数据。样式、精灵图及字形资源仍保留在 GL 缓存中，离线渲染需要另行处理。本项目不提供地图渲染器或瓦片服务器。

The archive contains tiles and metadata. Styles, sprites and glyphs remain
in the GL cache and require separate handling for offline rendering.
This project does not provide a map renderer or tile server.

### 直接查询缓存 / Direct cache queries

下载成功后，脚本会创建唯一的 `(z, x, y)` 索引。如果多个源模板或像素比导致 XYZ 查询存在歧义，脚本会报告错误并保留数据库。直接查询缓存时使用 XYZ 坐标：

After a successful download, the wrapper creates a unique `(z, x, y)` index.
It reports an error and keeps the database if multiple source templates or
pixel ratios make XYZ lookup ambiguous. Direct cache coordinates are XYZ:

```sql
SELECT data, compressed FROM tiles WHERE z=? AND x=? AND y=?;
```

`compressed=1` 的缓存瓦片记录使用 zlib 包装的 DEFLATE 数据流，不是 HTTP gzip 数据流。以 gzip 形式提供服务前，需要先解压或正确更换封装；MBTiles 导出器已执行所需的重新封装。

A cache record with `compressed=1` contains a zlib-wrapped DEFLATE stream,
not an HTTP gzip stream. Decompress it or rewrap it correctly before serving
it as gzip. The MBTiles exporter already performs the required repacking.

## 已知限制 / Known limitations

- 固定版本的原生下载器在发生 HTTP 身份验证错误或瓦片上限错误后，可能持续停留在事件循环中。请用 Ctrl-C 中断；已有数据库或部分进度不能视为下载完成。

  The frozen native downloader can remain in its event loop after an HTTP
  authentication error or tile-limit error. Interrupt it with Ctrl-C; do not
  interpret an existing database or partial progress as completion.

- 并发、重试和 HTTP 行为由原生下载器负责。下载脚本未实现可配置调度、持久化断点续传、下载超时或完整的下载后资源审计。脚本会检查原生退出状态、输出文件是否存在及 XYZ 唯一性；用户仍应按实际应用验证下载数据。

  Concurrency, retries and HTTP behavior are inherited from the native
  downloader. The wrapper does not implement configurable scheduling,
  durable resume, a download timeout or a complete post-download resource
  audit. The native exit status, output presence and XYZ uniqueness are
  checked; users should validate downloaded data for their application.

- 原生缓存对版本敏感。任意旧版或合并后的缓存，以及同一坐标包含多条瓦片记录的样式，不能直接等同于单数据源 MBTiles 归档。

  Native caches are version-sensitive. Arbitrary old or merged caches and
  styles with several tile rows at one coordinate are not interchangeable
  with a single-source MBTiles archive.

- 请勿将瓦片数据、字形、样式或凭据提交到本仓库，或打包到公开发行物中。

  Tile data, glyphs, styles and credentials must not be committed to this
  repository or bundled in its public releases.

## Agent 技能 / Agent skill

仓库在 [skills/mb-offline/SKILL.md](skills/mb-offline/SKILL.md) 提供可复用的 Agent 技能，覆盖区域估算、镜像准备、授权范围内的下载、缓存检查和 MBTiles 导出。技能使用当前检出的项目命令，并要求核对本地镜像的实际上限。

A reusable Agent skill is provided in
[skills/mb-offline/SKILL.md](skills/mb-offline/SKILL.md) for region estimates,
image preparation, authorized downloads, cache inspection and MBTiles export.
It uses the current checkout's commands and checks the selected image's actual
tile limit.

可将整个 `skills/mb-offline/` 目录安装到 Agent 支持的技能目录，也可以让 Agent 直接读取技能入口。安装副本仍需定位本项目的检出目录；宿主机工具需要 Python，原生下载需要 Docker，访问 Mapbox 资源时再提供本地凭据。示例请求：“使用 `$mb-offline` 先估算指定区域，再按已授权的范围执行下载或导出。”

Install the complete `skills/mb-offline/` directory through the Agent's skill
mechanism, or ask the Agent to read the entrypoint directly. An installed copy
still needs a checkout of this project, Python, Docker for native downloads,
and local credentials when the operation requires them. Example request:
“Use `$mb-offline` to estimate the specified region, then carry out the
authorized download or export.”

## 验证与 CI / Validation and CI

```sh
python3 -m unittest discover -s tests
python3 scripts/download.py --help
python3 scripts/export_mbtiles.py --help
sh scripts/check_image.sh mbgl-offline
```

Python 测试使用合成测试数据，不需要网络或 token。镜像检查禁用 Docker 网络，验证命令行帮助、动态库依赖、非 root 默认用户、随镜像分发的许可声明、构建工具是否已排除，以及使用调用者 UID/GID 写入挂载目录的能力。

The Python tests use synthetic fixtures and require no network or token.
The image check runs with Docker networking disabled and verifies CLI help,
dynamic dependencies, non-root defaults, bundled notices, absence of build
tools and writing to a bind mount with the caller's UID/GID.

GitHub Actions 已配置 Python 3.9/3.14 检查和 amd64/arm64 原生镜像构建。工作流使用只读仓库权限和固定提交版本的 Actions，不会推送镜像、创建发行版或调用 Mapbox API。构建仍需联网获取上游源码和 Ubuntu 软件包。贡献和验证约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

GitHub Actions is configured to run Python 3.9/3.14 checks and native amd64/arm64
image builds. The workflow has read-only repository permissions, uses pinned
action commits and does not push images, create releases or call Mapbox APIs.
Builds still need network access to upstream source repositories and Ubuntu
package mirrors. See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution and
verification rules.

## 许可证与来源 / License and provenance

项目原创部分采用 [Apache-2.0](LICENSE) 许可证。原生程序及其依赖保留各自的许可证，详见 [NOTICE](NOTICE)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [licenses/](licenses/README.md)。运行镜像中的许可声明位于 `/usr/local/share/mb-offline/`。

Original project contributions are licensed under [Apache-2.0](LICENSE).
The native executable and bundled dependencies retain their licenses; see
[NOTICE](NOTICE), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[licenses/](licenses/README.md). Notices are included in the runtime image at
`/usr/local/share/mb-offline/`.

[docs/provenance.md](docs/provenance.md) 记录了已确认的原生上游，以及推测历史 Docker 来源的证据。来源标注不代表存在隶属、背书或认可关系。

[docs/provenance.md](docs/provenance.md) records the confirmed native upstream
and the evidence for likely historical Docker references. Source attribution
does not imply affiliation or endorsement.
