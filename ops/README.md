# 公网服务器运维

在 Windows 运维环境安装 `paramiko`。使用 OpenSSH 从独立可信渠道核对服务器指纹，并将它保存在当前用户的 `~/.ssh/known_hosts`。工具不会自动接受未知主机。

- `python ops/pilot.py setup --host <公网IPv4> --user <SSH用户>`：首次设置或轮换凭据，在隐藏输入框输入 SSH 密码。真实配置写在项目的 `.local/server.json`。
- `python ops/pilot.py status`：自动读取配置并检查在线容器。
- `python ops/pilot.py deploy`：更新现有 nn-remote-pilot 容器，复用已有配置与挂载，保留原容器用于回滚，并检查配对接口和节点健康状态。

`server.example.json` 仅展示字段。真实密码由 Windows 当前用户的 DPAPI 保护，不写进 Git；更换电脑或 Windows 用户后须重新设置。部署凭据不打包进 Windows 或 iPhone 客户端。Git 忽略规则不用于撤回已经公开的凭据。
