import 'dart:convert';
import 'dart:io';

class DirectoryProfile {
  final Uri origin;
  final Map<String, String> names;
  DirectoryProfile._(this.origin, this.names);

  factory DirectoryProfile.parse(String source) {
    if (source.length > 65536) throw const FormatException('配置过长');
    final json = jsonDecode(source);
    if (json is! Map || json['signaling'] is! String || json['peers'] is! Map) {
      throw const FormatException('需要 signaling 和 peers 字段');
    }
    final uri = Uri.parse(json['signaling']);
    if (uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.path.isNotEmpty && uri.path != '/')) {
      throw const FormatException('信令服务需要 HTTPS 根地址');
    }
    final peers = json['peers'] as Map;
    final labels = json['device_names'];
    if (peers.length > 40 || (labels != null && labels is! Map)) {
      throw const FormatException('无效的设备列表');
    }
    final devices = <String, String>{};
    for (final key in peers.keys) {
      if (key is! String || key.trim().isEmpty || key.length > 128) {
        throw const FormatException('无效的设备 ID');
      }
      final name = labels?[key];
      devices[key] = name is String && name.trim().isNotEmpty ? name : key;
    }
    return DirectoryProfile._(uri, Map.unmodifiable(devices));
  }
}

Future<bool> signalReady(Uri origin) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
  Future<bool> probe() async {
    final request = await client.getUrl(origin.resolve('/healthz'));
    request.followRedirects = false;
    final response = await request.close();
    if (response.statusCode != 200) return false;
    final bytes = <int>[];
    await for (final chunk in response) {
      if (bytes.length + chunk.length > 2048) return false;
      bytes.addAll(chunk);
    }
    final result = jsonDecode(utf8.decode(bytes));
    return result is Map && result['status'] == 'ready';
  }

  try {
    return await probe().timeout(const Duration(seconds: 8));
  } on Exception {
    return false;
  } finally {
    client.close(force: true);
  }
}

Future<Map<String, bool>> readPresence(
    DirectoryProfile profile, String token) async {
  if (!RegExp(r'^[0-9a-fA-F]{64}$').hasMatch(token)) {
    throw const FormatException('设备令牌格式不正确');
  }
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
  Future<Map<String, bool>> read() async {
    final request =
        await client.postUrl(profile.origin.resolve('/v1/presence'));
    request.followRedirects = false;
    request.headers.contentType = ContentType.json;
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    request
        .add(utf8.encode(jsonEncode({'peers': profile.names.keys.toList()})));
    final response = await request.close();
    if (response.statusCode != 200) throw const HttpException('状态查询未获确认');
    final bytes = <int>[];
    await for (final chunk in response) {
      if (bytes.length + chunk.length > 16384) throw const FormatException();
      bytes.addAll(chunk);
    }
    final data = jsonDecode(utf8.decode(bytes));
    if (data is! Map || data['devices'] is! Map) throw const FormatException();
    final states = data['devices'] as Map;
    return {
      for (final key in profile.names.keys)
        if (states[key] is bool) key: states[key] as bool
    };
  }

  try {
    return await read().timeout(const Duration(seconds: 8));
  } finally {
    client.close(force: true);
  }
}
