import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nn_remote/artwork_page.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('fit preserves matte; cover selects the indicated edge', () async {
    final source = ui.PictureRecorder();
    final c = Canvas(source);
    c.drawColor(const Color(0xffff0000), BlendMode.src);
    c.drawRect(const Rect.fromLTWH(20, 0, 20, 10),
        Paint()..color = const Color(0xff0000ff));
    final first = source.endRecording();
    final image = await first.toImage(40, 10);
    first.dispose();
    Future<List<int>> pixel(BoxFit fit, Alignment pos, int offset) async {
      final rec = ui.PictureRecorder();
      paintArtwork(Canvas(rec), const Size(10, 10), image, fit, pos, 1);
      final pic = rec.endRecording();
      final im = await pic.toImage(10, 10);
      final rgba = await im.toByteData(format: ui.ImageByteFormat.rawRgba);
      final values = [
        for (var i = 0; i < 4; i++) rgba!.getUint8(offset * 4 + i)
      ];
      im.dispose();
      pic.dispose();
      return values;
    }

    try {
      expect(await pixel(BoxFit.cover, Alignment.centerRight, 55),
          [0, 0, 255, 255]);
      expect(await pixel(BoxFit.cover, Alignment.centerLeft, 55),
          [255, 0, 0, 255]);
      expect(
          await pixel(BoxFit.contain, Alignment.center, 0), [29, 41, 60, 255]);
    } finally {
      image.dispose();
    }
  });
}
