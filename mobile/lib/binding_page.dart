import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'bindings.dart';
import 'desktop_page.dart';
import 'profile.dart';

class BindingPage extends StatefulWidget {
  final DirectoryProfile profile;
  const BindingPage(this.profile, {super.key});
  @override
  State<BindingPage> createState() => _BindingPageState();
}

class _BindingPageState extends State<BindingPage> {
  final input = TextEditingController();
  String code = '', message = '';
  DateTime? until;
  Timer? timer;
  List<String> peers = [];
  bool busy = false;
  @override
  void initState() {
    super.initState();
    run('list');
  }

  Future<void> run(String action, [Map<String, String> data = const {}]) async {
    if (busy) return;
    setState(() => busy = true);
    try {
      final response = await bindingCall(widget.profile, action, data);
      if (!mounted) return;
      if (action == 'issue') {
        code = response['code'] as String;
        until = DateTime.now()
            .add(Duration(seconds: response['expires_in'] as int));
        timer?.cancel();
        timer = Timer.periodic(const Duration(seconds: 3), (_) {
          if (!DateTime.now().isBefore(until!)) {
            timer?.cancel();
            setState(() => code = '');
          }
          run('list');
        });
      }
      if (action == 'accept') input.clear();
      final listing = action == 'list'
          ? response
          : await bindingCall(widget.profile, 'list');
      if (!mounted) return;
      final updated = (listing['targets'] as List).cast<String>();
      if (listing['pending'] != true || action == 'remove') {
        timer?.cancel();
        code = '';
      }
      peers = updated;
      message = action == 'remove' ? '已解除绑定' : '';
    } catch (error) {
      if (mounted) message = '无法更新绑定：$error';
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    input.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: const Text('设备配对'), actions: [
        IconButton(
            tooltip: '刷新绑定设备',
            onPressed: busy ? null : () => run('list'),
            icon: const Icon(Icons.refresh))
      ]),
      body: ListView(padding: const EdgeInsets.all(20), children: [
        const Text('首次连接只需配对一次。双方任意一端都可以生成配对码。'),
        const SizedBox(height: 18),
        OutlinedButton(
            onPressed: busy ? null : () => run('issue'),
            child: const Text('生成手机配对码')),
        if (code.isNotEmpty) SelectableText('在电脑输入：$code（2 分钟内有效）'),
        const SizedBox(height: 18),
        TextField(
            controller: input,
            keyboardType: TextInputType.number,
            inputFormatters: [
              FilteringTextInputFormatter.digitsOnly,
              LengthLimitingTextInputFormatter(9)
            ],
            decoration: const InputDecoration(
                labelText: '电脑上的 9 位配对码', border: OutlineInputBorder())),
        FilledButton(
            onPressed: busy
                ? null
                : () {
                    if (input.text.length == 9) {
                      run('accept', {'code': input.text});
                    } else {
                      setState(() => message = '请输入完整的 9 位配对码');
                    }
                  },
            child: const Text('绑定电脑')),
        if (message.isNotEmpty) Text(message),
        const Divider(height: 36),
        Text('已绑定电脑（${peers.length}）',
            style: Theme.of(context).textTheme.titleMedium),
        for (final id in peers)
          ListTile(
              leading: const Icon(Icons.desktop_windows_outlined),
              title: Text(widget.profile.names[id] ?? id),
              subtitle: const Text('连接远程桌面'),
              onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) => DesktopPage(
                          widget.profile, id, widget.profile.names[id] ?? id,
                          bound: true))),
              trailing: IconButton(
                  tooltip: '解除绑定',
                  icon: const Icon(Icons.link_off),
                  onPressed: busy
                      ? null
                      : () async {
                          final yes = await showDialog<bool>(
                              context: context,
                              builder: (ctx) => AlertDialog(
                                      title: const Text('解除设备绑定？'),
                                      content: const Text('此电脑上的当前配对会话也将断开。'),
                                      actions: [
                                        TextButton(
                                            onPressed: () => Navigator.pop(ctx),
                                            child: const Text('取消')),
                                        TextButton(
                                            onPressed: () =>
                                                Navigator.pop(ctx, true),
                                            child: const Text('解除绑定'))
                                      ]));
                          if (yes == true && mounted) {
                            await run('remove', {'target': id});
                          }
                        })),
      ]));
}
