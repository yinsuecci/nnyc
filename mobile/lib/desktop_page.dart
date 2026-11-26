import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'rtc_session.dart';
import 'profile.dart';
import 'ice_path.dart';
import 'dart:typed_data';
import 'artwork_page.dart';

Offset? desktopPoint(Size area, Size video, Offset tap) {
  if (video.isEmpty || area.isEmpty) return null;
  final size = applyBoxFit(BoxFit.contain, video, area).destination;
  final rect = Alignment.center.inscribe(size, Offset.zero & area);
  if (!rect.contains(tap)) return null;
  return Offset(
      (tap.dx - rect.left) / rect.width, (tap.dy - rect.top) / rect.height);
}

class DesktopPage extends StatefulWidget {
  final DirectoryProfile profile;
  Uri get origin => profile.origin;
  final String target, name;
  const DesktopPage(this.profile, this.target, this.name, {super.key});
  @override
  State<DesktopPage> createState() => _DesktopPageState();
}

class _DesktopPageState extends State<DesktopPage> with WidgetsBindingObserver {
  final renderer = RTCVideoRenderer();
  final credential = TextEditingController();
  DesktopSession? session;
  String status = '';
  bool firstFrame = false;
  IcePath path = IcePath.unknown;
  Uint8List? draft;
  bool uploading = false;

  Future<void> editArtwork() async {
    final image = await Navigator.push<Uint8List>(
        context, MaterialPageRoute(builder: (_) => ArtworkPage(widget.name)));
    if (mounted && image != null) setState(() => draft = image);
  }

  Future<void> applyArtwork() async {
    final current = session;
    if (current == null || draft == null || uploading) return;
    setState(() => uploading = true);
    var notice = '未收到电脑保存确认';
    try {
      final saved = await current.applyArtwork(draft!);
      notice = saved ? '已保存到目标电脑，未开启遮屏' : '电脑已拒绝保存';
    } catch (_) {}
    if (!mounted) return;
    setState(() => uploading = false);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(notice)));
  }

  Timer? frameDeadline;
  bool ready = false;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    initialize();
  }

  Future<void> initialize() async {
    try {
      await renderer.initialize();
      if (!mounted) {
        await renderer.dispose();
        return;
      }
      renderer.onFirstFrameRendered = () {
        frameDeadline?.cancel();
        if (mounted && session?.closed == false) {
          setState(() => firstFrame = true);
        }
      };
      setState(() => ready = true);
    } catch (_) {
      if (mounted) setState(() => status = '无法启动视频渲染');
    }
  }

  Future<void> connect() async {
    final token = credential.text.trim();
    if (!RegExp(r'^[0-9]{9}$').hasMatch(token.replaceAll(' ', ''))) {
      setState(() => status = '请输入电脑上显示的 9 位连接码');
      return;
    }
    credential.clear();
    late final DesktopSession next;
    next = DesktopSession(
        widget.profile, widget.target, token.replaceAll(' ', ''), (s) {
      if (mounted && session == next) {
        if (next.closed) {
          renderer.srcObject = null;
          firstFrame = false;
          session = null;
        }
        setState(() => status = s);
      }
    }, (s) {
      if (mounted && session == next) renderer.srcObject = s;
    }, (value) {
      if (mounted && session == next && path != value) {
        setState(() => path = value);
      }
    });
    setState(() {
      session = next;
      path = IcePath.unknown;
      firstFrame = false;
      status = '正在请求连接';
    });
    try {
      await next.start();
      if (mounted && session == next && !firstFrame) {
        frameDeadline = Timer(const Duration(seconds: 15), () {
          if (mounted && session == next && !firstFrame) {
            stop();
            setState(() => status = '没有收到桌面画面，本次连接已结束');
          }
        });
      }
    } catch (e) {
      if (mounted && session == next) {
        setState(() {
          session = null;
          status = '连接失败：$e';
        });
      }
    }
  }

  void stop() {
    frameDeadline?.cancel();
    final old = session;
    if (old == null) return;
    setState(() {
      session = null;
      firstFrame = false;
      status = '已断开';
    });
    renderer.srcObject = null;
    unawaited(old.close());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) stop();
  }

  @override
  void dispose() {
    frameDeadline?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    final old = session;
    session = null;
    if (old != null) unawaited(old.close());
    if (ready) unawaited(renderer.dispose());
    credential.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: Text(widget.name), actions: [
        if (session == null)
          IconButton(
              onPressed: editArtwork,
              icon: const Icon(Icons.image_outlined),
              tooltip: '编辑目标电脑隐私屏图片'),
        if (firstFrame && draft != null && session != null)
          TextButton(
              onPressed: uploading ? null : applyArtwork,
              child: Text(uploading ? '等待电脑确认' : '应用图片')),
      ]),
      body: SafeArea(
          child: Column(children: [
        if (session == null)
          if (draft != null) const Text('隐私屏草稿已就绪，连接后可申请保存'),
        if (session == null)
          Padding(
              padding: const EdgeInsets.all(16),
              child: TextField(
                  controller: credential,
                  keyboardType: TextInputType.number,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: const InputDecoration(
                      labelText: '电脑连接码',
                      helperText: '在 Windows 客户端点击「手机连接码」'))),
        if (!firstFrame && (session?.verificationCode.isNotEmpty ?? false))
          SelectableText('本次校验码：${session!.verificationCode}'),
        Text(firstFrame && session?.closed == false
            ? '正在共享 · ${path.label}'
            : status),
        if (firstFrame && session?.closed == false)
          Text(session!.privacyConfirmed ? '电脑报告：桌面已遮挡并静音' : '未确认电脑端遮屏'),
        if (session == null)
          FilledButton(
              onPressed: ready ? connect : null, child: const Text('请求桌面共享')),
        if (session != null)
          Expanded(
              child: LayoutBuilder(
                  builder: (context, size) => GestureDetector(
                      onTapUp: (e) {
                        final pos = desktopPoint(
                            size.biggest,
                            Size(renderer.videoWidth.toDouble(),
                                renderer.videoHeight.toDouble()),
                            e.localPosition);
                        if (firstFrame && pos != null) {
                          session?.tap((pos.dx * 65535).round(),
                              (pos.dy * 65535).round());
                        }
                      },
                      child: ColoredBox(
                          color: Colors.black,
                          child: RTCVideoView(renderer))))),
        if (session != null)
          TextButton(onPressed: stop, child: const Text('断开连接')),
      ])));
}
