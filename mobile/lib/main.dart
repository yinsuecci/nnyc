import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'profile.dart';
import 'directory_store.dart';
import 'desktop_page.dart';
import 'dart:io';
import 'binding_page.dart';

void main() => runApp(const NNRemote());

const blue = Color(0xff0759ed);
Widget icon(String name,
        {Color color = const Color(0xff526173), double size = 24}) =>
    Image.asset('assets/fluent/$name.png',
        width: size, height: size, color: color);

class NNRemote extends StatelessWidget {
  const NNRemote({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'nn远程',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
            useMaterial3: true,
            colorSchemeSeed: blue,
            scaffoldBackgroundColor: Colors.white),
        home: const DirectoryPage(),
      );
}

class DirectoryPage extends StatefulWidget {
  const DirectoryPage({super.key});
  @override
  State<DirectoryPage> createState() => _DirectoryPageState();
}

class _DirectoryPageState extends State<DirectoryPage> {
  DirectoryProfile? profile;
  int revision = 0;
  String query = '', status = '';
  final search = TextEditingController();
  @override
  void initState() {
    super.initState();
    restore();
  }

  Future<void> restore() async {
    final initial = revision;
    try {
      final saved = await DirectoryStore.load();
      if (mounted && revision == initial && saved != null) {
        setState(() {
          profile = saved;
          status = '已恢复通讯录';
        });
      }
    } catch (_) {
      if (mounted && revision == initial) {
        setState(() => status = '无法读取本地通讯录，可重新导入');
      }
    }
  }

  Future<void> forget() async {
    final yes = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
                title: const Text('清除本机通讯录？'),
                content: const Text('不会删除服务器上的设备或更改配对权限。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('清除'))
                ]));
    if (yes != true || !mounted) return;
    try {
      await DirectoryStore.write(null);
    } catch (_) {
      if (mounted) setState(() => status = '清除失败，请重试');
      return;
    }
    if (!mounted) return;
    ++revision;
    search.clear();
    setState(() {
      profile = null;
      query = '';
      status = '已清除本机通讯录';
    });
  }

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  Future<void> importDirectory() async {
    final next = await Navigator.push<DirectoryProfile>(
        context, MaterialPageRoute(builder: (_) => const ImportPage()));
    if (!mounted || next == null) return;
    search.clear();
    final request = ++revision;
    setState(() {
      profile = next;
      query = '';
      status = '正在检查信令服务';
    });
    final ready = await signalReady(next);
    if (!mounted || revision != request) return;
    setState(() => status = ready ? '信令服务可达' : '信令服务未就绪或无法验证');
  }

  @override
  Widget build(BuildContext context) {
    final devices = profile?.names.entries
            .where((e) => '${e.key} ${e.value}'
                .toLowerCase()
                .contains(query.toLowerCase()))
            .toList() ??
        [];
    return Scaffold(
        appBar: AppBar(title: const Text('设备通讯录'), actions: [
          IconButton(
              tooltip: '导入设备通讯录',
              onPressed: importDirectory,
              icon: icon('settings')),
          if (profile != null)
            PopupMenuButton<String>(
                tooltip: '更多选项',
                onSelected: (_) => forget(),
                itemBuilder: (_) => [
                      const PopupMenuItem(
                          value: 'forget', child: Text('清除本机通讯录'))
                    ]),
        ]),
        body: SafeArea(
            child: ListView(padding: const EdgeInsets.all(20), children: [
          TextField(
              controller: search,
              decoration: InputDecoration(
                  prefixIcon: Padding(
                      padding: const EdgeInsets.all(12),
                      child: icon('search', size: 20)),
                  hintText: '搜索设备（名称或设备 ID）',
                  border: const OutlineInputBorder()),
              onChanged: (s) => setState(() => query = s.trim())),
          const SizedBox(height: 24),
          if (profile != null && Platform.isIOS)
            FilledButton.icon(
                onPressed: () => Navigator.push(context,
                    MaterialPageRoute(builder: (_) => BindingPage(profile!))),
                icon: const Icon(Icons.link),
                label: const Text('配对与已绑定电脑')),
          Text('我的设备  (${devices.length})'),
          const SizedBox(height: 12),
          if (profile == null)
            OutlinedButton(
                onPressed: importDirectory, child: const Text('导入设备通讯录')),
          if (profile != null && devices.isEmpty) const Text('没有匹配的设备'),
          for (final e in devices)
            ListTile(
                contentPadding: const EdgeInsets.symmetric(vertical: 4),
                leading: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                        color: const Color(0xffe4eeff),
                        borderRadius: BorderRadius.circular(10)),
                    child: icon('desktop', color: blue)),
                title: Text(e.value),
                subtitle: const Text('使用电脑连接码连接'),
                onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                        builder: (_) => Scaffold(
                            appBar: AppBar(title: Text(e.value)),
                            body: ListView(
                                padding: const EdgeInsets.all(20),
                                children: [
                                  ListTile(
                                      leading: icon('desktop', color: blue),
                                      title: const Text('设备 ID'),
                                      subtitle: SelectableText(e.key),
                                      trailing: IconButton(
                                          tooltip: '复制设备 ID',
                                          onPressed: () => Clipboard.setData(
                                              ClipboardData(text: e.key)),
                                          icon: icon('copy'))),
                                  const Divider(),
                                  FilledButton(
                                      onPressed: () => Navigator.push(
                                          context,
                                          MaterialPageRoute(
                                              builder: (_) => Platform.isIOS
                                                  ? BindingPage(profile!)
                                                  : DesktopPage(profile!, e.key,
                                                      e.value))),
                                      child: const Text('连接远程桌面')),
                                  const ListTile(
                                      title: Text('连接信息'),
                                      subtitle: Text('未建立远程会话')),
                                ]))))),
          if (status.isNotEmpty)
            Padding(
                padding: const EdgeInsets.only(top: 24), child: Text(status)),
        ])));
  }
}

class ImportPage extends StatefulWidget {
  const ImportPage({super.key});
  @override
  State<ImportPage> createState() => _ImportPageState();
}

class _ImportPageState extends State<ImportPage> {
  final input = TextEditingController();
  String? error;
  bool remember = false, saving = false;
  @override
  void dispose() {
    input.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: const Text('导入通讯录')),
      body: SafeArea(
          child: ListView(padding: const EdgeInsets.all(20), children: [
        const Text('仅导入可信来源的配置。可包含该信令地址专用的公开证书，不需要令牌或私钥。'),
        const SizedBox(height: 16),
        TextField(
            controller: input,
            smartQuotesType: SmartQuotesType.disabled,
            smartDashesType: SmartDashesType.disabled,
            onChanged: (_) {
              if (error != null) setState(() => error = null);
            },
            minLines: 5,
            maxLines: 10,
            autocorrect: false,
            enableSuggestions: false,
            decoration: InputDecoration(
                labelText: '设备配置 JSON',
                errorText: error,
                border: const OutlineInputBorder())),
        const SizedBox(height: 16),
        SwitchListTile(
            title: const Text('在此设备记住通讯录'),
            subtitle: const Text('可保存信令公开证书，不保存令牌、私钥或在线状态'),
            value: remember,
            onChanged: saving ? null : (v) => setState(() => remember = v)),
        FilledButton(
            onPressed: saving
                ? null
                : () async {
                    setState(() => saving = true);
                    try {
                      final parsed = DirectoryProfile.parse(input.text);
                      await DirectoryStore.write(remember ? parsed : null);
                      if (context.mounted) Navigator.pop(context, parsed);
                    } on FormatException catch (e) {
                      if (mounted) {
                        setState(() {
                          saving = false;
                          error = e.message;
                        });
                      }
                    } catch (_) {
                      if (mounted) {
                        setState(() {
                          saving = false;
                          error = '无法更新本机通讯录，请重试。';
                        });
                      }
                    }
                  },
            child: const Text('导入')),
      ])));
}
