import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nn_remote/main.dart';
import 'package:nn_remote/profile.dart';
import 'package:nn_remote/directory_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));
  const source =
      '{"signaling":"https://signal.example.invalid","peers":{"bob":"cert.pem"},"device_names":{"bob":"办公室电脑"}}';
  test('remembers only allowed fields, then erases the directory', () async {
    final imported = source.replaceFirst(
        '"signaling"', '"token":"test-secret","key":"private.pem","signaling"');
    await DirectoryStore.write(DirectoryProfile.parse(imported));
    final data =
        (await SharedPreferences.getInstance()).getString(DirectoryStore.key)!;
    for (final forbidden in [
      'test-secret',
      'private.pem',
      'cert.pem',
      '"token"'
    ]) {
      expect(data, isNot(contains(forbidden)));
    }
    expect((await DirectoryStore.load())!.names, {'bob': '办公室电脑'});
    await DirectoryStore.write(null);
    expect(await DirectoryStore.load(), isNull);
  });
  test('reads desktop contacts without treating certificate paths as names',
      () {
    expect(DirectoryProfile.parse(source).names, {'bob': '办公室电脑'});
    expect(() => DirectoryProfile.parse(source.replaceFirst('https:', 'http:')),
        throwsFormatException);
  });
  testWidgets('imports a directory and opens the selected device',
      (tester) async {
    await tester.pumpWidget(const NNRemote());
    await tester.tap(find.text('导入设备通讯录'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, source);
    await tester.tap(find.text('导入'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('办公室电脑'));
    await tester.pumpAndSettle();
    expect(find.text('bob'), findsOneWidget);
    expect(find.text('未建立远程会话'), findsOneWidget);
    expect(await DirectoryStore.load(), isNull);
  });
  testWidgets('opt in, restart and forget contacts', (tester) async {
    await tester.pumpWidget(const NNRemote());
    await tester.tap(find.text('导入设备通讯录'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, source);
    await tester.tap(find.text('在此设备记住通讯录'));
    await tester.tap(find.text('导入'));
    await tester.pumpAndSettle();
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(const NNRemote());
    await tester.pumpAndSettle();
    expect(find.text('办公室电脑'), findsOneWidget);
    expect(find.text('使用电脑连接码连接'), findsOneWidget);
    await tester.tap(find.byTooltip('更多选项'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('清除本机通讯录'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('清除'));
    await tester.pumpAndSettle();
    expect(find.text('办公室电脑'), findsNothing);
    expect(await DirectoryStore.load(), isNull);
  });
  testWidgets('device directory never prompts for a long-lived credential',
      (tester) async {
    await tester.pumpWidget(const NNRemote());
    await tester.tap(find.text('导入设备通讯录'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, source);
    await tester.tap(find.text('导入'));
    await tester.pumpAndSettle();
    expect(find.byTooltip('查询设备状态'), findsNothing);
    expect(find.text('使用电脑连接码连接'), findsOneWidget);
  });
}
