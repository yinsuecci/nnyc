import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nn_remote/desktop_page.dart';

void main() {
  test('ignores letterbox and maps displayed pixels rather than screen bounds',
      () {
    const a = Size(400, 400), v = Size(1920, 1080);
    expect(desktopPoint(a, v, const Offset(40, 30)), isNull);
    expect(desktopPoint(a, v, const Offset(200, 200)), const Offset(.5, .5));
    expect(desktopPoint(a, Size.zero, const Offset(0, 0)), isNull);
  });
}
