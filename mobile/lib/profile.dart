import 'dart:convert';
import 'dart:io';

class DirectoryProfile {
  final Uri origin;
  final Map<String, String> names;
  final String? signalCaDer;
  DirectoryProfile._(this.origin, this.names, this.signalCaDer);

  HttpClient client() {
    final ca = signalCaDer;
    if (ca == null) return HttpClient();
    final context = SecurityContext(withTrustedRoots: false);
    final pemBody =
        RegExp(r'.{1,64}').allMatches(ca).map((m) => m[0]).join('\n');
    final pem = ascii.encode(
        '-----BEGIN CERTIFICATE-----\n$pemBody\n-----END CERTIFICATE-----\n');
    try {
      context.setTrustedCertificatesBytes(pem);
    } on TlsException {
      if (!Platform.isIOS) rethrow;
      context.setTrustedCertificatesBytes(base64Decode(ca));
    }
    return HttpClient(context: context);
  }

  factory DirectoryProfile.parse(String source) {
    if (source.length > 65536) throw const FormatException('配置过长');
    final dynamic json;
    try {
      json = jsonDecode(source.trim().replaceFirst(RegExp(r'^\uFEFF'), ''));
    } on FormatException {
      throw const FormatException('JSON 格式错误，请使用完整的 phone.json 内容');
    }
    if (json is! Map || json['signaling'] is! String || json['peers'] is! Map) {
      throw const FormatException('需要 signaling 和 peers 字段');
    }
    final uri = Uri.tryParse(json['signaling']);
    if (uri == null || uri.scheme != 'https' ||
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
    final ca = json['signal_ca_der'];
    if (ca != null) {
      try {
        if (ca is! String || ca.isEmpty || ca.length > 24576 ||
            base64Encode(base64Decode(ca)) != ca) {
          throw const FormatException();
        }
      } on FormatException {
        throw const FormatException('信令证书编码无效，请使用原始 phone.json');
      }
    }
    final profile =
        DirectoryProfile._(uri, Map.unmodifiable(devices), ca as String?);
    if (ca != null) {
      try {
        profile.client().close(force: true);
      } catch (_) {
        throw const FormatException('无效的信令证书');
      }
    }
    return profile;
  }
}

Future<bool> signalReady(DirectoryProfile profile) async {
  final origin = profile.origin;
  final client = profile.client()
    ..connectionTimeout = const Duration(seconds: 5);
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
    return result is Map &&
        (result['status'] == 'ready' || result['ready'] == true);
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
  final client = profile.client()
    ..connectionTimeout = const Duration(seconds: 5);
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
