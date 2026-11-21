import 'dart:typed_data';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

void paintArtwork(Canvas canvas, Size size, ui.Image photo, BoxFit fit,
    Alignment anchor, double zoom) {
  final srcSize = Size(photo.width.toDouble(), photo.height.toDouble());
  final fitted = applyBoxFit(fit, srcSize, size);
  final source = anchor.inscribe(
      fitted.source / (fit == BoxFit.cover ? zoom : 1), Offset.zero & srcSize);
  final destination =
      Alignment.center.inscribe(fitted.destination, Offset.zero & size);
  canvas.drawColor(const Color(0xff1d293c), BlendMode.src);
  canvas.drawImageRect(
      photo, source, destination, Paint()..filterQuality = FilterQuality.high);
}

class _Artwork extends CustomPainter {
  final ui.Image photo;
  final BoxFit fit;
  final Alignment anchor;
  final double zoom;
  _Artwork(this.photo, this.fit, this.anchor, this.zoom);
  @override
  void paint(Canvas canvas, Size size) =>
      paintArtwork(canvas, size, photo, fit, anchor, zoom);
  @override
  bool shouldRepaint(_Artwork old) =>
      old.photo != photo ||
      old.fit != fit ||
      old.anchor != anchor ||
      old.zoom != zoom;
}

class ArtworkPage extends StatefulWidget {
  final String device;
  const ArtworkPage(this.device, {super.key});
  @override
  State<ArtworkPage> createState() => _ArtworkPageState();
}

class _ArtworkPageState extends State<ArtworkPage> {
  ui.Image? photo;
  BoxFit fit = BoxFit.cover;
  Alignment anchor = Alignment.center;
  double zoom = 1;
  String status = '仅预览，尚未发送到电脑';
  bool busy = false;

  Future<void> pick() async {
    setState(() => busy = true);
    try {
      final file = await ImagePicker().pickImage(
          source: ImageSource.gallery,
          maxWidth: 2048,
          maxHeight: 2048,
          imageQuality: 88,
          requestFullMetadata: false);
      if (file == null) return;
      if (await file.length() > 12 * 1024 * 1024) throw const FormatException();
      final buffer =
          await ui.ImmutableBuffer.fromUint8List(await file.readAsBytes());
      try {
        final descriptor = await ui.ImageDescriptor.encoded(buffer);
        try {
          if (descriptor.width * descriptor.height > 20000000) {
            throw const FormatException();
          }
          final codec = await descriptor.instantiateCodec();
          try {
            final decoded = (await codec.getNextFrame()).image;
            if (!mounted) {
              decoded.dispose();
              return;
            }
            final old = photo;
            setState(() {
              photo = decoded;
              anchor = Alignment.center;
              zoom = 1;
              status = '仅预览，尚未发送到电脑';
            });
            old?.dispose();
          } finally {
            codec.dispose();
          }
        } finally {
          descriptor.dispose();
        }
      } finally {
        buffer.dispose();
      }
    } catch (_) {
      if (mounted) setState(() => status = '无法读取这张图片');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> finish() async {
    setState(() => busy = true);
    try {
      final recorder = ui.PictureRecorder();
      paintArtwork(
          Canvas(recorder), const Size(1280, 720), photo!, fit, anchor, zoom);
      final picture = recorder.endRecording();
      try {
        final output = await picture.toImage(1280, 720);
        try {
          final bytes = await output.toByteData(format: ui.ImageByteFormat.png);
          if (bytes == null) throw const FormatException();
          if (mounted) {
            Navigator.pop<Uint8List>(
                context,
                bytes.buffer
                    .asUint8List(bytes.offsetInBytes, bytes.lengthInBytes));
          }
        } finally {
          output.dispose();
        }
      } finally {
        picture.dispose();
      }
    } catch (_) {
      if (mounted) setState(() => status = '无法生成预览图');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  void dispose() {
    photo?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: const Text('编辑隐私屏图片')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Text(widget.device),
        Text(status),
        AspectRatio(
            aspectRatio: 16 / 9,
            child: LayoutBuilder(
                builder: (context, area) => GestureDetector(
                    onPanUpdate: (e) => setState(() => anchor = Alignment(
                        (anchor.x - e.delta.dx * 2 / area.maxWidth)
                            .clamp(-1.0, 1.0),
                        (anchor.y - e.delta.dy * 2 / area.maxHeight)
                            .clamp(-1.0, 1.0))),
                    child: ColoredBox(
                        color: const Color(0xff1d293c),
                        child: photo == null
                            ? null
                            : CustomPaint(
                                painter:
                                    _Artwork(photo!, fit, anchor, zoom)))))),
        OutlinedButton(
            onPressed: busy ? null : pick, child: const Text('从相册选择')),
        SegmentedButton<BoxFit>(segments: const [
          ButtonSegment(value: BoxFit.cover, label: Text('填充')),
          ButtonSegment(value: BoxFit.contain, label: Text('完整显示'))
        ], selected: {
          fit
        }, onSelectionChanged: (s) => setState(() => fit = s.first)),
        Slider(
            value: zoom,
            min: 1,
            max: 3,
            onChanged:
                fit == BoxFit.cover ? (v) => setState(() => zoom = v) : null),
        FilledButton(
            onPressed: busy || photo == null ? null : finish,
            child: const Text('保留本次草稿'))
      ]));
}
