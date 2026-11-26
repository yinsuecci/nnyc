import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'ice_path.dart';
import 'profile.dart';

/// One explicitly authorized Windows desktop; no microphone, camera or disk credentials.
class DesktopSession {
  DesktopSession(DirectoryProfile profile, this.target, this._code,
      this.onStatus, this.onTrack, this.onPath,
      {this.bound = false})
      : origin = profile.origin,
        _http =
            (profile.client()..connectionTimeout = const Duration(seconds: 4));
  final Uri origin;
  final String target;
  final bool bound;
  String _code;
  String _token = '';
  final void Function(String) onStatus;
  final void Function(MediaStream) onTrack;
  final void Function(IcePath) onPath;
  bool _readingPath = false;
  final HttpClient _http;
  RTCPeerConnection? pc;
  RTCDataChannel? input;
  RTCDataChannel? artwork;
  StreamController<String>? _replies;
  String? sid;
  String verificationCode = '';
  bool closed = false, inputAllowed = false, _polling = false;
  Timer? _timer;
  Timer? _privacyExpiry;
  bool privacyConfirmed = false;

  void check() {
    if (closed) throw const HttpException('会话已结束');
  }

  Future<Map<String, dynamic>> _call(
      String action, Map<String, dynamic> body) async {
    final req = await _http.postUrl(origin.resolve('/v1/mobile/$action'));
    req.followRedirects = false;
    req.headers.contentType = ContentType.json;
    if (_token.isNotEmpty) {
      req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_token');
    }
    req.add(utf8.encode(jsonEncode(body)));
    final res = await req.close();
    if (res.statusCode != 200) {
      await res.drain<void>();
      throw _SignalError(res.statusCode);
    }
    final bytes = <int>[];
    await for (final chunk in res) {
      if (bytes.length + chunk.length > 65536) throw const FormatException();
      bytes.addAll(chunk);
    }
    return jsonDecode(utf8.decode(bytes)) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> call(String action,
          [Map<String, dynamic> body = const {}]) =>
      _call(action, body).timeout(const Duration(seconds: 6));

  Future<void> start() async {
    try {
      if (bound) _token = _code;
      final config = await call(bound ? 'connect' : 'redeem',
          {'target': target, if (!bound) 'code': _code});
      _code = '';
      sid = config['id'] as String;
      _token = config['session_key'] as String;
      check();
      final port = config['stun_port'];
      final ice = <Map<String, String>>[];
      if (port is int && port > 0 && port <= 65535) {
        final host =
            origin.host.contains(':') ? '[${origin.host}]' : origin.host;
        ice.add({'urls': 'stun:$host:$port'});
      }
      final peer = await createPeerConnection({'iceServers': ice});
      if (closed) {
        await peer.close();
        return;
      }
      pc = peer;
      peer.onTrack = (event) {
        if (!closed &&
            event.track.kind == 'video' &&
            event.streams.isNotEmpty) {
          onTrack(event.streams.first);
        }
      };
      await peer.addTransceiver(
          kind: RTCRtpMediaType.RTCRtpMediaTypeVideo,
          init:
              RTCRtpTransceiverInit(direction: TransceiverDirection.RecvOnly));
      final channel =
          await peer.createDataChannel('nn-input', RTCDataChannelInit());
      input = channel;
      artwork =
          await peer.createDataChannel('nn-artwork', RTCDataChannelInit());
      artwork!.onMessage = (m) {
        if (!m.isBinary && _replies?.isClosed == false) _replies!.add(m.text);
      };
      artwork!.onDataChannelState = (s) {
        if (s == RTCDataChannelState.RTCDataChannelClosed) _replies?.close();
      };
      channel.onMessage = (m) {
        if (closed || m.isBinary) return;
        if (m.text == 'input:allowed' || m.text == 'input:view') {
          inputAllowed = m.text == 'input:allowed';
        } else if (m.text == 'privacy:verified') {
          _privacyExpiry?.cancel();
          if (!privacyConfirmed) {
            privacyConfirmed = true;
            onStatus('被控端已确认遮屏和静音');
          }
          _privacyExpiry = Timer(const Duration(seconds: 3), () {
            privacyConfirmed = false;
            if (!closed) onStatus('隐私状态未确认');
          });
        }
      };
      final gathered = Completer<void>();
      peer.onIceGatheringState = (s) {
        if (s == RTCIceGatheringState.RTCIceGatheringStateComplete &&
            !gathered.isCompleted) {
          gathered.complete();
        }
      };
      await peer.setLocalDescription(await peer.createOffer());
      await gathered.future.timeout(const Duration(seconds: 12));
      final offer = (await peer.getLocalDescription())!.sdp!;
      final prints = RegExp(
              r'^a=fingerprint:sha-256 ((?:[0-9a-f]{2}:){31}[0-9a-f]{2})\r?$',
              multiLine: true,
              caseSensitive: false)
          .allMatches(offer)
          .map((m) => m.group(1)!.replaceAll(':', '').toUpperCase())
          .toSet();
      if (prints.length != 1) throw const FormatException('设备指纹不可用');
      verificationCode = prints.single.substring(0, 16);
      check();
      check();
      onStatus('等待 Windows 用户授权');
      bool published = false;
      final deadline = DateTime.now().add(const Duration(seconds: 60));
      for (int i = 0; DateTime.now().isBefore(deadline); i++) {
        check();
        try {
          if (!published) {
            await call('rtc_publish', {'id': sid, 'sdp': offer});
            published = true;
            onStatus('对端已授权，正在协商连接');
          }
          final answer = (await call('rtc_read', {'id': sid}))['sdp'];
          if (answer is String) {
            check();
            await peer
                .setRemoteDescription(RTCSessionDescription(answer, 'answer'));
            check();
            onStatus('等待远端画面');
            _timer = Timer.periodic(const Duration(seconds: 2), (_) => _poll());
            return;
          }
        } on _SignalError catch (e) {
          if (e.status != 409 || published) rethrow;
        }
        await Future<void>.delayed(const Duration(seconds: 1));
      }
      throw TimeoutException('桌面授权或连接超时');
    } catch (_) {
      await close();
      rethrow;
    }
  }

  Future<void> _poll() async {
    if (_polling || closed) return;
    _polling = true;
    try {
      await call('active', {'id': sid});
      unawaited(_readPath());
    } catch (_) {
      final notify = !closed;
      await close();
      if (notify) onStatus('授权失效或网络中断，已断开');
    } finally {
      _polling = false;
    }
  }

  Future<void> _readPath() async {
    final peer = pc;
    if (closed || peer == null || _readingPath) return;
    _readingPath = true;
    try {
      final stats = await peer.getStats().timeout(const Duration(seconds: 2));
      if (!closed) onPath(videoIcePath(stats));
    } on Exception {
      if (!closed) onPath(IcePath.unknown);
    } finally {
      _readingPath = false;
    }
  }

  Future<void> tap(int x, int y) async {
    if (closed ||
        !inputAllowed ||
        input?.state != RTCDataChannelState.RTCDataChannelOpen) return;
    for (final e in [
      ['move', x, y],
      ['button', 'left', true],
      ['button', 'left', false]
    ]) {
      await input!.send(RTCDataChannelMessage(jsonEncode(e)));
    }
  }

  Future<bool> applyArtwork(Uint8List png) async {
    final dc = artwork;
    if (closed ||
        !inputAllowed ||
        dc?.state != RTCDataChannelState.RTCDataChannelOpen ||
        png.isEmpty ||
        png.length > 4 * 1024 * 1024 ||
        _replies != null) {
      throw StateError('当前会话无法更换图片');
    }
    final replies = StreamController<String>();
    final incoming = StreamIterator(replies.stream);
    _replies = replies;
    Future<String> receive(Duration timeout) async {
      if (!await incoming.moveNext().timeout(timeout)) {
        throw StateError('连接已关闭');
      }
      return incoming.current;
    }

    const wait = Duration(seconds: 8);
    try {
      await dc!.send(RTCDataChannelMessage(jsonEncode({'bytes': png.length})));
      if (await receive(wait) != 'ready') throw const FormatException();
      for (int pos = 0; pos < png.length; pos += 16384) {
        check();
        await dc.send(RTCDataChannelMessage.fromBinary(Uint8List.sublistView(
            png, pos, (pos + 16384).clamp(0, png.length))));
        if (await receive(wait) != 'next') throw const FormatException();
      }
      await dc.send(RTCDataChannelMessage('finish'));
      if (await receive(wait) != 'review') throw const FormatException();
      final result = await receive(const Duration(seconds: 65));
      if (result == 'declined') return false;
      if (result != 'saved') throw const FormatException();
      return true;
    } catch (_) {
      await dc?.close();
      rethrow;
    } finally {
      _replies = null;
      await incoming.cancel();
      unawaited(replies.close());
    }
  }

  Future<void> close() async {
    if (closed) return;
    closed = true;
    privacyConfirmed = false;
    _privacyExpiry?.cancel();
    if (_replies != null) unawaited(_replies!.close());
    inputAllowed = false;
    _timer?.cancel();
    try {
      await pc?.close();
    } finally {
      try {
        if (sid != null) await call('cancel', {'id': sid});
      } catch (_) {}
      _token = '';
      _http.close(force: true);
    }
  }
}

class _SignalError implements Exception {
  final int status;
  const _SignalError(this.status);
  @override
  String toString() => switch (status) {
        401 => '连接码已失效或会话已结束，请在电脑上获取新码',
        403 => '设备绑定已解除，请重新配对',
        429 => '尝试过于频繁，请稍后再试',
        404 => '设备离线或会话已结束',
        422 => '对端未启用移动桌面共享',
        409 => '当前设备身份被占用或会话不可用',
        _ => '信令请求失败（$status）'
      };
}
