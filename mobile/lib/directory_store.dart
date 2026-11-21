import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import 'profile.dart';

class DirectoryStore {
  static const key = 'nn.directory.v1';

  static Future<DirectoryProfile?> load() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(key);
    return saved == null ? null : DirectoryProfile.parse(saved);
  }

  static Future<void> write(DirectoryProfile? profile) async {
    final prefs = await SharedPreferences.getInstance();
    if (profile == null && !prefs.containsKey(key)) return;
    // Rebuild from validated display fields; never serialize the import document.
    final ok = profile == null
        ? await prefs.remove(key)
        : await prefs.setString(
            key,
            jsonEncode({
              'signaling': profile.origin.origin,
              'peers': {for (final id in profile.names.keys) id: ''},
              'device_names': profile.names
            }));
    if (!ok) throw StateError('通讯录存储失败');
  }
}
