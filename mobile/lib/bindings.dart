import 'dart:convert';
import 'dart:io';
import 'package:flutter/services.dart';
import 'profile.dart';

Future<String> phoneIdentity(DirectoryProfile profile) async {
  final key = await const MethodChannel('nn.remote/binding')
      .invokeMethod<String>('identity', profile.origin.origin);
  if (key == null || key.length != 64) throw StateError('手机身份不可用');
  return key;
}

Future<Map<String, dynamic>> bindingCall(
    DirectoryProfile profile, String action,
    [Map<String, String> body = const {}]) async {
  final key = await phoneIdentity(profile);
  final client = profile.client()
    ..connectionTimeout = const Duration(seconds: 5);
  Future<Map<String, dynamic>> send() async {
    final req = await client
        .postUrl(profile.origin.resolve('/v1/mobile/binding_$action'));
    req.followRedirects = false;
    req.headers.contentType = ContentType.json;
    req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $key');
    req.add(utf8.encode(jsonEncode(body)));
    final res = await req.close();
    if (res.statusCode != 200) {
      throw HttpException(switch (res.statusCode) {
        401 => '配对码已失效，请重新生成',
        404 => '设备已解绑或服务器尚未更新',
        429 => '请求过于频繁，请稍后再试',
        _ => '配对服务返回 HTTP ${res.statusCode}'
      });
    }
    return jsonDecode(await utf8.decoder.bind(res).join())
        as Map<String, dynamic>;
  }

  try {
    return await send().timeout(const Duration(seconds: 8));
  } finally {
    client.close(force: true);
  }
}
