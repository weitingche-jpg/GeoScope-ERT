# GeoScope ERT

GeoScope 的电阻率成像（ERT）教学与工作流验证工具。这个源码快照对应 Windows x64 电法体验版 **2026-11-19**。

软件支持导入地形、测线、电极和观测表，检查观测数据，使用 pyGIMLi 进行二维电阻率反演，以及在三维场景中查看地形与剖面。解译时请区分二维剖面的空间展示、剖面插值和联合三维反演。合成案例只用于教学与验证。

## 从源码运行

在 Windows x64 上使用 **64 位 Python 3.12**。打开 PowerShell 并切到解压后的 `GeoScope-ERT` 目录。依赖安装需要访问 PyPI。

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-lock.txt
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe app.py
```

打开后在第一步选择「加载完整合成演示」。试用自己的表格时，可参考 `data/observation-samples`。操作说明见 [快速开始](快速开始.md)。

## 构建 Windows 便携版

安装上面的锁定依赖后，在同一个目录运行下面的命令。PyInstaller 分析并打包应用后，复制演示数据与许可文件。

```powershell
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm GeoScope-ERT.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败' }

$dest = '.\dist\GeoScope-ERT'
Copy-Item -LiteralPath '.\data', '.\third-party-licenses', '.\快速开始.md' -Destination $dest -Recurse
& "$dest\GeoScope.exe"
```

完整的便携版在 `dist/GeoScope-ERT/`。分享或归档时保留整个目录，包含 `GeoScope.exe`、`_internal`、`data` 和第三方许可。

## 运行回归测试

```powershell
& .\.venv\Scripts\python.exe -m unittest test_line_import test_survey_project test_dipole_ert test_ert_qc_pipeline test_project_bundle
```

涉及课堂原始观测及论文配套数据的用例会跳过。部分随原版保留的内部桌面验收参数也需要这些外部数据。日常启动、公开合成演示及用户自主导入数据独立于这些验收数据。

## 示例数据与许可

- `data/terrain.tif` 是 PyVista 示例数据中的圣海伦斯 DEM 裁剪，归属和处理过程见 [来源说明](data/provenance.json)。
- 虚拟校园、演示电极、地下模型和观测模板用于教学。示例观测与地下异常均为合成。
- 随包第三方组件和数据的许可见 [组件及来源](third-party-licenses/组件及来源.md)。GeoScope 自有代码的许可证由权利人确认后添加。
