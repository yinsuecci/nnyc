import 'dart:io';
import '../lib/profile.dart';

// Each input comes from the test harness, never a production profile.
Future<void> main(List<String> args) async {
  for (final path in args) {
    final profile = DirectoryProfile.parse(await File(path).readAsString());
    final expected = path == args.first;
    if (await signalReady(profile) != expected) {
      throw StateError('Unexpected TLS verification result: $path');
    }
  }
}
