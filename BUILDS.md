# 构建记录

`main` 分支每次 Build Manager 构建成功后，**Archive Build** 工作流会把这次的全部产物（管理器 APK、各 KMI 的 LKM、ksud、ksuinit、混淆映射）永久保存到对应的 GitHub Release，并在下表登记一行。Actions 页面上的产物 90 天后会被 GitHub 删除，Release 不会。

- **构建号**：KernelSU 版本号（30000 + 提交数），管理器首页、「关于」页和内核日志里显示的就是它。
- **产物**：链接到该次构建的 Release，Release 说明（同时附带 `BUILD_INFO.md`）里有完整版本信息和每个文件的 SHA-256。
- **管理器和内核要配套**：内核 / LKM 只认表中同一行的包名和签名证书。

表格由 CI 自动维护，请不要手动修改两个标记之间的内容。

<!-- BUILDS:START -->

| 构建号 | 构建时间 (UTC+8) | 提交 | 上游基线 | 管理器包名 | 签名证书 | 产物 |
|---|---|---|---|---|---|---|

<!-- BUILDS:END -->
