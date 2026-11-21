# nn远程移动客户端

Android 和 iOS 共用 Flutter 通讯录。当前版本可导入桌面配置的公开字段、搜索设备、
复制设备 ID，并检查信令服务的 HTTPS 健康状态。工具栏可单次查询在线状态：
核对服务地址后输入本机设备令牌，不保存令牌，也不发布手机监听端口。
信令配置示例：`"directory_grants":{"phone":["office","home"]}`。
没有授权或未知的设备不返回状态；查询结果 20 秒后过期。此授权仅用于查询，
不代表会话授权。移动端远控画面和文件传输尚未接入。

导入示例：`{"signaling":"https://signal.example.invalid","peers":{"office":""},"device_names":{"office":"办公室电脑"}}`。
默认仅在本次打开期间保留通讯录。导入时可选择在此设备记住服务地址、设备 ID 和显示名称；
应用重启后不会沿用旧在线状态。「更多选项 → 清除本机通讯录」移除本地副本，不更改服务端权限。
健康检查要求有效的受信任证书，不跟随重定向。

项目目录中运行 `flutter analyze` 和 `flutter test`；Android 测试包使用
`flutter build apk --debug`。正式 Android 签名从环境变量
`NN_ANDROID_KEYSTORE`、`NN_ANDROID_KEY_ALIAS`、`NN_ANDROID_STORE_PASSWORD`
和 `NN_ANDROID_KEY_PASSWORD` 读取，不使用调试密钥替代正式签名。
iOS 需在配置 Xcode 的 macOS 构建机验证及签名。
