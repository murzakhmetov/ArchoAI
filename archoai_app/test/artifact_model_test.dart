import 'package:archoai_app/models/artifact_model.dart';
import 'package:flutter_test/flutter_test.dart';

ArtifactModel _artifact({String? localModelPath, String? tripoTaskId}) =>
    ArtifactModel(
      id: 'a1',
      name: 'Vase',
      imagePath: '/images/a1.jpg',
      modelUrl: 'https://cdn.example/m.glb',
      localModelPath: localModelPath,
      tripoTaskId: tripoTaskId,
      createdAt: DateTime.utc(2026, 1, 1),
    );

void main() {
  group('ArtifactModel Tripo task id', () {
    test('survives the local JSON round trip', () {
      final restored = ArtifactModel.fromJson(
        _artifact(tripoTaskId: 'task-1').toJson(),
      );

      expect(restored.tripoTaskId, 'task-1');
    });

    test('a database row has no task id and parses as null', () {
      final row = ArtifactModel.fromJson({
        'id': 'a1',
        'name': 'Vase',
        'image_path': '',
        'created_at': '2026-01-01T00:00:00Z',
      });

      expect(row.tripoTaskId, isNull);
      expect(row.localModelPath, isNull);
    });

    test('copyWith sets and clears the task id', () {
      final withTask = _artifact().copyWith(tripoTaskId: 'task-1');
      expect(withTask.tripoTaskId, 'task-1');
      expect(withTask.copyWith(name: 'Renamed').tripoTaskId, 'task-1');
      expect(withTask.copyWith(clearTripoTaskId: true).tripoTaskId, isNull);
    });

    test('copyWith can clear a local model path that is no longer valid', () {
      final stale = _artifact(localModelPath: '/other-device/a1.glb');

      expect(stale.copyWith(clearLocalModelPath: true).localModelPath, isNull);
      expect(stale.copyWith(name: 'x').localModelPath, '/other-device/a1.glb');
    });
  });
}
