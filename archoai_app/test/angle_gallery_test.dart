import 'package:archoai_app/models/artifact_model.dart';
import 'package:archoai_app/widgets/angle_gallery.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('ArtifactModel.photoUrls', () {
    Map<String, dynamic> row(Object? photos) => {
          'id': 'a1',
          'name': 'Vessel',
          'image_path': 'http://pi/a.jpg',
          'created_at': '2026-11-28T13:04:39Z',
          'photo_urls': photos,
        };

    test('reads the six angle photos in order', () {
      final urls = [for (var d = 0; d < 360; d += 60) 'http://pi/scans/x/angle_$d.jpg'];
      expect(ArtifactModel.fromJson(row(urls)).photoUrls, urls);
    });

    test('missing column, null or odd values give an empty list', () {
      expect(ArtifactModel.fromJson(row(null)).photoUrls, isEmpty);
      expect(ArtifactModel.fromJson(row('not a list')).photoUrls, isEmpty);
      expect(ArtifactModel.fromJson(row(['ok', 3, '', null])).photoUrls, ['ok']);
      final noColumn = row(null)..remove('photo_urls');
      expect(ArtifactModel.fromJson(noColumn).photoUrls, isEmpty);
    });

    test('survives the local cache (toJson / fromJson) and copyWith', () {
      final a = ArtifactModel.fromJson(row(['u0', 'u60']));
      expect(ArtifactModel.fromJson(a.toJson()).photoUrls, ['u0', 'u60']);
      expect(a.copyWith(name: 'New').photoUrls, ['u0', 'u60']);
    });
  });

  test('angle labels for six photos', () {
    expect([for (var i = 0; i < 6; i++) angleLabel(i, 6)], ['0°', '60°', '120°', '180°', '240°', '300°']);
  });

  testWidgets('strip shows one thumbnail per angle and opens the gallery', (tester) async {
    final urls = [for (var i = 0; i < 6; i++) 'http://invalid.test/angle_$i.jpg'];
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: AngleStrip(urls: urls))));
    expect(find.text('Ракурсы: 6'), findsOneWidget);
    expect(find.text('0°'), findsOneWidget);
    expect(find.byKey(const ValueKey('angle-thumb-0')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('angle-thumb-0')));
    await tester.pumpAndSettle();
    expect(find.byType(AngleGalleryScreen), findsOneWidget);
    expect(find.text('0°  ·  1 / 6'), findsOneWidget);

    await tester.fling(find.byType(PageView), const Offset(-400, 0), 1000);
    await tester.pumpAndSettle();
    expect(find.text('60°  ·  2 / 6'), findsOneWidget);
  });

  testWidgets('no photos: the strip takes no space', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: AngleStrip(urls: []))));
    expect(find.textContaining('Ракурсы'), findsNothing);
  });
}
